define(['app/sfc/sfc.module'], function (sfc) {

  sfc.register.controller('servicePathCtrl', function ($scope, $rootScope, ServiceFunctionSvc, ServicePathSvc, ServicePathHelper, ModalDeleteSvc, ngTableParams, $filter, $timeout) {
    var NgTableParams = ngTableParams; // checkstyle 'hack'
    var sfpsGetDataWait = true;  // wait for combining unpersisted with persisted in getArray callback

    $scope.tableParams = new NgTableParams(
      {
        page: 1,            // show first page
        count: 10           // count per page
      },
      {
        total: 0, // wait for 'sfcs'
        getData: function ($defer, params) {
          if (!sfpsGetDataWait && params.total() > 0) {
            // use build-in angular filter
            var orderedData = params.sorting() ?
              $filter('orderBy')($scope.sfps, params.orderBy()) :
              $scope.sfps;

            $defer.resolve(orderedData.slice((params.page() - 1) * params.count(), params.page() * params.count()));
          } else {
            $defer.resolve([]);
          }
        }
      }
    );

    $timeout(function () {
      $scope.$watchCollection('sfps', function (newVal) {
        if (angular.isUndefined(newVal)) {
          return;
        }
        $scope.tableParams.total($scope.sfps.length);
        $scope.tableParams.reload();
      });
    });

    $scope.getSFPstate = function getSFPstate(sfp) {
      return sfp.state || $rootScope.sfpState.PERSISTED;
    };

    $scope.setSFPstate = function setSFPstate(sfp, newState) {
      if (angular.isDefined(sfp.state) && sfp.state === $rootScope.sfpState.NEW) {
        sfp.state = $rootScope.sfpState.NEW;
      }
      else {
        sfp.state = newState;
      }
    };

    $scope.unsetSFPstate = function unsetSFPstate(sfp) {
      delete sfp.state;
    };

    $scope.isSFPstate = function isSFPstate(sfp, state) {
      return $scope.getSFPstate(sfp) === state ? true : false;
    };

    ServiceFunctionSvc.getArray(function (data) {
      $scope.sfs = data;

      $scope.tableParamsSfName = new NgTableParams({
          page: 1,            // show first page
          count: 10           // count per page
        },
        {
          counts: [10, 25], // hide page counts control
          total: $scope.sfs.length,  // value less than count hide pagination
          getData: function ($defer, params) {
            // use build-in angular filter
            var orderedData = params.sorting() ?
              $filter('orderBy')($scope.sfs, params.orderBy()) :
              $scope.sfs;

            $defer.resolve(orderedData.slice((params.page() - 1) * params.count(), params.page() * params.count()));
          }
        });
    });

    ServicePathSvc.getArray(function (data) {

      //temporary SFPs are kept in rootScope, persisted SFPs should be removed from it
      var tempSfps = [];
      _.each($rootScope.sfps, function (sfp) {
        if ($scope.getSFPstate(sfp) !== $rootScope.sfpState.PERSISTED) {
          tempSfps.push(sfp);
        }
      });

      //concat temporary with loaded data (dont add edited sfcs which are already in tempSfps)
      if (angular.isDefined(data)) {
        _.each(data, function (sfp) {
          //if it is not in tempSfps add it
          if (angular.isUndefined(_.findWhere(tempSfps, {name: sfp.name}))) {
            $scope.setSFPstate(sfp, $rootScope.sfpState.PERSISTED);
            ServicePathHelper.orderHopsInSFP(sfp);
            tempSfps.push(sfp);
          }
        });
      }

      $rootScope.sfps = tempSfps;
      sfpsGetDataWait = false;
    });

    $scope.undoSFPchanges = function undoSFPchanges(sfp) {
      ServicePathSvc.getItem(sfp.name, function (oldSfp) {
        var index = _.indexOf($rootScope.sfps, sfp);
        $rootScope.sfps.splice(index, 1);
        $rootScope.sfps.splice(index, 0, oldSfp);
      });
    };

    $scope.sortableOptions = {
      connectWith: ".connected-apps-container",
      cursor: 'move',
      placeholder: 'place',
      tolerance: 'pointer',
      start: function (e, ui) {
        $(e.target).data("ui-sortable").floating = true;
      },
      update: function (e, ui) {
        //sfc[0]: name, sfc[1]: index in sfcs
        var sfp = $(e.target).closest('tr').attr('id').split("~!~");
        $scope.setSFPstate(_.findWhere($rootScope.sfps, {"name": sfp[0]}), $rootScope.sfpState.EDITED);
      },
      helper: function (e, ui) {
        var elms = ui.clone();
        elms.find(".arrow-right-part").addClass("arrow-empty");
        elms.find(".arrow-left-part").addClass("arrow-empty");
        return elms;
      }
    };

    $scope.onSFPdrop = function ($sf, sfp) {
      if (sfp['service-path-hop'] === undefined) {
        sfp['service-path-hop'] = [];
      }
      sfp['service-path-hop'].push({"service-function-name": $sf});

      $scope.setSFPstate(sfp, $rootScope.sfpState.EDITED);
    };

    $scope.deleteSFP = function deleteSFP(sfp) {
      ModalDeleteSvc.open(sfp.name, function (result) {
        if (result == 'delete') {
          //delete the row
          ServicePathSvc.deleteItem(sfp, function () {
            $rootScope.sfps.splice(_.indexOf($rootScope, sfp), 1);
          });
        }
      });
    };

    $scope.removeSFfromSFP = function removeSFfromSFP(sfp, index) {
      sfp['service-path-hop'].splice(index, 1);
      $scope.setSFPstate(sfp, $rootScope.sfpState.EDITED);
    };

    $scope.persistSFP = function persistSFP(sfp) {
      $scope.unsetSFPstate(sfp);
      ServicePathHelper.updateHopsOrderInSFP(sfp);
      ServicePathHelper.updateStartingIndexOfSFP(sfp);
      ServicePathSvc.putItem(sfp, function () {
      });
    };

    $scope.getSFindexInSFS = function getSFindexInSFS(sfName) {
      var sfObject = _.findWhere($scope.sfs, {name: sfName});
      return _.indexOf($scope.sfs, sfObject);
    };
  });

});