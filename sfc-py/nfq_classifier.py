# hint: to see the rules execute: 'sudo iptables -S -t raw'

from netfilterqueue import NetfilterQueue
from nsh_decode import *
from nsh_encode import *
import subprocess
import logging
# from logging_config import *
import socket
import threading
from sff_globals import *
from sfc_agent import find_sff_locator

logger = logging.getLogger('sfc.' + __name__)

NFQ_NUMBER = 1
TUNNEL_ID = 0x0500  # TO DO add tunnel_id to sff yang model
SUDO = True

# global ref to manager
nfq_class_manager = None


def get_nfq_class_manager_ref():
    global nfq_class_manager
    return nfq_class_manager


# common command executor
def execute_cli(cli):
    if (SUDO):
        cli = "sudo " + cli
    logger.debug("execute_cli: %s", cli)
    subprocess.call([cli], shell=True)
    return


# To DO - implement class like this like global helper class managing path infos
class NfqPathInfoSupplier:
    def __init__(self):
        self.path_name_2_id_map = {}
        self.path_id_2_info_map = {}

    def get_path_id(self, path_name):
        if path_name in self.path_name_2_id_map:
            return self.path_name_2_id_map[path_name]

        if self.__add_path_info(path_name):
            return self.get_path_id(path_name)  # call this one once more
        else:
            logger.warn('get_path_id: path not found (path_name=%s)', path_name)
            return None

    def delete_path_info(self, path_id):
        # remove data from maps for given path
        if path_id in self.path_id_2_info_map:
            path_item = self.path_id_2_info_map.pop(path_id)
            path_name = path_item['name']
            if path_name in self.path_name_2_id_map:
                self.path_name_2_id_map.pop(path_name)
                return True
        else:
            logger.debug('delete_path_info: path not found (path_id=%d)', path_id)

        return False

    # private  - returns True if path_item was found in global path data
    def __add_path_info(self, path_name):
        if not get_path():
            logger.warn('__add_path_info: No path data')
            return False

        if path_name in get_path():
            path_item = get_path()[path_name]
            path_id = path_item['path-id']
            self.path_name_2_id_map[path_name] = path_id
            self.path_id_2_info_map[path_id] = path_item
            return True

        return False

    # assuming info already added by requesting path_id before
    def get_forwarding_params(self, path_id):
        if path_id not in self.path_id_2_info_map:
            logger.warn('get_forwarding_params: path data not found for path_id=%d', path_id)
            return None

        path_item = self.path_id_2_info_map[path_id]

        # string ref for sff for first hop
        sff_name = path_item['rendered-service-path-hop'][0]['service-function-forwarder']

        sff_locator = find_sff_locator(sff_name)
        if not sff_locator:
            logger.warn('get_forwarding_params: sff data not found for sff_name=%s', sff_name)

        return sff_locator

    # assuming info already added by requesting path_id before
    def get_tunnel_params(self, path_id):
        if path_id not in self.path_id_2_info_map:
            logger.warn('get_tunnel_params: path data not found for path_id=%d', path_id)
            return None

        path_item = self.path_id_2_info_map[path_id]

        if 'context-metadata' in path_item:
            ctx_metadata = path_item['context-metadata']
            ctx_values = CONTEXTHEADER(ctx_metadata['context-header1'], ctx_metadata['context-header2'],
                                       ctx_metadata['context-header3'], ctx_metadata['context-header4'])
        else:
            ctx_values = CONTEXTHEADER(0, 0, 0, 0)  # empty default ctx

        # set path_id, starting-index to VXLAN+NSH template
        tunnel_params = {
            "vxlan_values": VXLANGPE(int('00000100', 2), 0, 0x894F, TUNNEL_ID, 64),
            "base_values": BASEHEADER(0x1, int('01000000', 2), 0x6, 0x1, 0x1, path_id, path_item['starting-index']),
            "ctx_values": ctx_values
        }

        return tunnel_params


class NfqClassifierManager:
    # we use mark that will be equal to path_id
    def __init__(self, nfq_number):
        self.nfq_number = nfq_number
        self.nfqueue = NetfilterQueue()
        self.__reset()
        return

    # private reset
    def __reset(self):
        self.path_info_supp = NfqPathInfoSupplier()
        self.clear_all_rules()
        return

    # wannabe destructor - does not work
    def __del__(self):
        self.clear_all_rules()
        # NetfilterQueue should destroy itself properly automatically
        return

    # deletes all forwarder and iptables rules
    def clear_all_rules(self):
        logger.info("clear_all_rules: Reset iptables rules.")
        # init map
        self.path_id_2_pfw_map = {}
        # clear all previous rules/sub-chains in 'raw' table
        cli = "iptables -t raw -F"
        execute_cli(cli)
        cli = "iptables -t raw -X"
        execute_cli(cli)
        return

    # helper
    def get_sub_chain_name(self, path_id):
        return "sfp-nfq-" + str(path_id)

    # main NFQ callback for received packets
    def common_process_packet(self, packet):
        try:
            logger.debug("common_process_packet: received packet=%s, mark=%d", packet, packet.get_mark())

            mark = packet.get_mark()

            # check
            if mark in self.path_id_2_pfw_map:
                packet_forwarder = self.path_id_2_pfw_map[mark]
                packet_forwarder.process_packet(packet)
            else:
                logger.warn("common_process_packet: no packet forwarder for mark=%d, dropping the packet", mark)
                packet.drop()

            return
        except Exception as e:
            logger.exception('common_process_packet: exception')

    def run(self):
        self.nfqueue.bind(self.nfq_number, self.common_process_packet)

        logger.info("NFQ binded to queue number %d", self.nfq_number)

        self.nfqueue.run()
        return

    # recompile_all_acls -  list
    def recompile_all_acls(self, acls):
        self.__reset()

        collected_results = {}  # add error info to this dictionary

        for acl_item in acls['access-lists']['access-list']:
            self.compile_one_acl(acl_item, collected_results)

        return collected_results  # return info about unsuccesful ace-s

    # compile_one_acl
    # !assumed! all aces in alc_item are for one and only path
    def compile_one_acl(self, acl_item, collected_results):
        logger.debug("compile_one_acl: acl_item=%s", acl_item)

        if not collected_results:
            collected_results = {}  # add error info to this dictionary

        path_id = None

        for ace in acl_item['access-list-entries']:

            # first ace in list
            if path_id == None:
                path_name = ace['actions']['service-function-acl:service-function-path']
                path_id = self.path_info_supp.get_path_id(path_name)

                if not path_id:
                    logger.error("compile_one_acl: path_id not found for path_name=%s", path_name)
                    collected_results[path_name] = 'Path data not found'
                    return collected_results

                logger.debug("compile_one_acl: found path_id=%d", path_id)

                # delete possible former forwarder
                if path_id in self.path_id_2_pfw_map:
                    self.destroy_packet_forwarder(path_id)

                # init new forwarder
                self.init_new_packet_forwarder(path_id)

            self.add_iptables_classification_rule(ace, path_id)

        return collected_results  # return info about unsuccesful ace-s


    def get_packet_forwarder(self, path_id):
        try:
            return self.path_id_2_pfw_map[path_id]
        except:
            logger.warn('get_packet_forwarder: None for path_id=%d', path_id)
            return None


    def init_new_packet_forwarder(self, path_id):
        sub_chain_name = self.get_sub_chain_name(path_id)

        # create sub-chain for the path, this way we can in future easily remove the rules for particular path
        cli = "iptables -t raw -N " + sub_chain_name
        execute_cli(cli)
        # insert jump to sub-chain
        cli = "iptables -t raw -I PREROUTING -j " + sub_chain_name
        execute_cli(cli)
        # add jump to queue 1 in case of match mark (ACL matching rules will have to be inserted before this one)
        cli = "iptables -t raw -A " + sub_chain_name + " -m mark --mark " + str(
            path_id) + " -j NFQUEUE --queue-num " + str(self.nfq_number)
        execute_cli(cli)

        packet_forwarder = PacketForwarder(path_id)

        packet_forwarder.update_forwarding_params(self.path_info_supp.get_forwarding_params(path_id))
        packet_forwarder.update_tunnel_params(self.path_info_supp.get_tunnel_params(path_id))

        self.path_id_2_pfw_map[path_id] = packet_forwarder
        return


    # create iptables matches for sending packets to NFQ of given number
    def add_iptables_classification_rule(self, ace, path_id):
        assert ace
        assert path_id

        ace_matches = ace['matches']

        # dl_src
        dl_src = ''
        if ('source-mac-address' in ace_matches):
            dl_src = '-m mac --mac-source' + ace_matches['source-mac-address']
            if ('source-mac-address-mask' in ace_matches):
                logger.warn('source-mac-address-mask match not implemented')

        # dl_dst
        dl_dst = ''
        if ('destination-mac-address' in ace_matches):
            logger.warn('destination-mac-address match not implemented')

        # nw_src/ipv6_src
        nw_src = ''
        if ('source-ipv4-address' in ace_matches):
            nw_src = ' -s ' + ace_matches['source-ipv4-address']

        ipv6_src = ''
        if ('source-ipv6-address' in ace_matches):
            ipv6_src = ' -s ' + ace_matches['source-ipv6-address']  # not sure about this

        #nw_dst/ipv6_dst
        nw_dst = ''
        if ('destination-ipv4-address' in ace_matches):
            nw_dst = ' -d ' + ace_matches['destination-ipv4-address']

        ipv6_dst = ''
        if ('destination-ipv6-address' in ace_matches):
            ipv6_dst = ' -d ' + ace_matches['destination-ipv6-address']  # not sure about this

        # nw_proto --- TCP/UDP ....
        nw_proto = ''
        if ('ip-protocol' in ace_matches):
            if ace_matches['ip-protocol'] == 7:
                nw_proto = ' -p tcp'
            elif ace_matches['ip-protocol'] == 17:
                nw_proto = ' -p udp'
            else:
                logger.warn('unknown ip-protocol=%d', ace_matches['ip-protocol'])

        # only lower transport port dst/src supported !!!!
        tp_dst = ''
        if ('destination-port-range' in ace_matches):
            if ('lower-port' in ace_matches['destination-port-range']):
                if nw_proto == '':
                    logger.error(
                        "add_iptables_classification_rule: processing 'destination-port-range'. ip-protocol must be specified")
                    return
                tp_dst = ' --dport ' + str(ace_matches['destination-port-range']['lower-port'])

        tp_src = ''
        if ('source-port-range' in ace_matches):
            if ('lower-port' in ace_matches['source-port-range']):
                if nw_proto == '':
                    logger.error(
                        "add_iptables_classification_rule: processing 'source-port-range'. ip-protocol must be specified")
                    return
                tp_src = ' --sport ' + str(ace_matches['source-port-range']['lower-port'])

        sub_chain_name = self.get_sub_chain_name(path_id)

        # 'I' - insert this 'set mark' rule before the 'jump to queue' rule
        cli = "iptables -t raw -I " + sub_chain_name
        cli += nw_src + nw_dst + ipv6_src + ipv6_dst + nw_proto + tp_src + tp_dst
        cli += " -j MARK --set-mark " + str(path_id)

        execute_cli(cli)
        return

    # destroy PacketForwader with iptables rules and chains
    def destroy_packet_forwarder(self, path_id):
        # check
        assert path_id

        # delete info from path_info_supplier instance
        self.path_info_supp.delete_path_info(path_id)

        if path_id in self.path_id_2_pfw_map:
            logger.debug("destroy_packet_forwarder: Removing classifier for path_id=%d", path_id)

            del self.path_id_2_pfw_map[path_id]

            sub_chain_name = get_sub_chain_name(path_id)

            # -D - delete the jump to sub-chain
            cli = "iptables -t raw -D PREROUTING -j " + sub_chain_name
            execute_cli(cli)
            # delete rules in sub-chain
            cli = "iptables -t raw -F  " + sub_chain_name
            execute_cli(cli)
            # delete sub-chain
            cli = "iptables -t raw -X " + sub_chain_name
            execute_cli(cli)

            logger.info("destroy_packet_forwarder: Classifier for path_id=%d removed", path_id)
        else:
            logger.debug("destroy_packet_forwarder: Classifier for path_id=%d not found", path_id)


class PacketForwarder:
    def __init__(self, path_id):
        self.path_id = path_id
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._fw_params_set = False
        self._tun_params_set = False
        return

    def __str__(self):
        return "PacketForwarder: path_id=" + str(self.path_id)

    def update_forwarding_params(self, forwarding_params):
        if not forwarding_params:
            return
        self.sff_ip_addr = forwarding_params['ip']
        self.sff_port = forwarding_params['port']
        self._fw_params_set = True
        return

    def update_tunnel_params(self, tunnel_params):
        if not tunnel_params:
            return
        self.vxlan_values = tunnel_params['vxlan_values']
        self.base_values = tunnel_params['base_values']
        self.ctx_values = tunnel_params['ctx_values']
        self._tun_params_set = True
        return

    def process_packet(self, orig_packet):
        # check
        if not self._fw_params_set:
            logger.error('process_packet: Forwarding params not set for path_id=%d', self.path_id)
            return

        if not self._tun_params_set:
            logger.error('process_packet: Tunnel params not set for path_id=%d', self.path_id)
            return

        logger.debug('process_packet: Forwarding packet to %s:%d', self.sff_ip_addr, self.sff_port)

        orig_payload = orig_packet.get_payload()
        vxlan_packet = build_packet(self.vxlan_values, self.base_values, self.ctx_values) + orig_payload
        self.socket.sendto(vxlan_packet, (self.sff_ip_addr, self.sff_port))
        orig_packet.drop()  # ! drop original packet
        return


# globals

def start_nfq_class_manager():
    global nfq_class_manager

    if nfq_class_manager:
        logger.error('Nfq classifier already started in other thread!')

    nfq_class_manager = NfqClassifierManager(NFQ_NUMBER)
    nfq_class_manager.run()
    return


def start_nfq_classifier():
    global nfq_class_manager

    if nfq_class_manager:
        logger.error('Nfq classifier already started in other thread!')

    logger.info('starting nfq classifier')
    t = threading.Thread(target=start_nfq_class_manager, args=())
    t.daemon = True
    t.start()
    return
