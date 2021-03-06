/**
 * Copyright (c) 2014 Ericsson India Global Services Pvt Ltd. and others.  All rights reserved.
 *
 * This program and the accompanying materials are made available under the
 * terms of the Eclipse Public License v1.0 which accompanies this distribution,
 * and is available at http://www.eclipse.org/legal/epl-v10.html
 *
 */

package org.opendaylight.ofsfc.provider;

import org.opendaylight.controller.md.sal.binding.api.DataBroker;
import org.opendaylight.controller.md.sal.binding.api.DataChangeListener;
import org.opendaylight.controller.md.sal.common.api.data.LogicalDatastoreType;
import org.opendaylight.yangtools.concepts.ListenerRegistration;
import org.opendaylight.yangtools.yang.binding.InstanceIdentifier;

public abstract class OpenflowAbstractDataListener implements DataChangeListener {

    private DataBroker dataBroker;
    private InstanceIdentifier<?> iID;
    private ListenerRegistration<DataChangeListener> dataChangeListenerRegistration;

    public void setDataBroker(DataBroker dataBroker) {
        this.dataBroker = dataBroker;
    }

    public void setIID(InstanceIdentifier<?> IID) {
        iID = IID;
    }

    public DataBroker getDataBroker() {
        return dataBroker;
    }

    public void registerAsDataChangeListener() {
        dataChangeListenerRegistration = dataBroker.registerDataChangeListener(LogicalDatastoreType.CONFIGURATION, iID,
                this, DataBroker.DataChangeScope.SUBTREE);
    }

    public void closeDataChangeListener() {
        dataChangeListenerRegistration.close();
    }

}
