// FH1 Radio: the station logo, previous and next, and the radio's own volume. fh1Radio.lua sends Fh1RadioState and takes the hooks below.
// radio.py installs this as ui/modules/apps/FH1Radio in the fh1_radio mod, with FH1's logos beside it.
angular.module('beamng.apps')
.directive('fh1Radio', [function () {
  return {
    template:
      '<div class="bngApp" style="width:100%; height:100%; box-sizing:border-box; padding:6px 8px; display:flex; flex-direction:column; justify-content:center; color:#fff; font-family:\'Segoe UI\', Arial, sans-serif">' +
        '<div ng-if="!state.running" style="text-align:center; opacity:.7; font-size:13px">FH1 radio plays on the FH1 levels</div>' +
        '<div ng-if="state.running" style="display:flex; flex-direction:column; align-items:center; width:100%">' +
          '<div style="display:flex; align-items:center; justify-content:space-between; width:100%; height:68px">' +
            '<div ng-click="tune(-1)" style="cursor:pointer; padding:8px; font-size:18px; opacity:.85">&#9664;</div>' +
            '<img ng-if="state.tuned > 0" ng-src="{{ state.stations[state.tuned - 1].logo }}" style="height:68px">' +
            '<div ng-if="state.tuned == 0" style="display:flex; align-items:center; font-weight:700; letter-spacing:.15em; opacity:.8">' +
              '<img ng-src="{{ state.offLogo }}" style="height:26px; margin-right:8px">RADIO OFF</div>' +
            '<div ng-click="tune(1)" style="cursor:pointer; padding:8px; font-size:18px; opacity:.85">&#9654;</div>' +
          '</div>' +
          '<div style="font-size:14px; font-weight:600; max-width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">{{ state.title }}</div>' +
          '<div style="font-size:12px; opacity:.8; max-width:100%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">{{ state.artist }}</div>' +
          '<div style="display:flex; align-items:center; width:100%; margin-top:6px">' +
            '<span style="font-size:11px; font-weight:700; letter-spacing:.1em; margin-right:6px">VOL</span>' +
            '<input type="range" min="0" max="{{ maxPercent }}" step="1" ng-model="ui.volume" ng-change="setVolume()" style="flex:1; cursor:pointer">' +
            '<span style="font-size:11px; width:38px; text-align:right">{{ ui.volume }}%</span>' +
          '</div>' +
        '</div>' +
      '</div>',
    replace: true,
    link: function (scope) {
      scope.state = { running: false, tuned: 1, stations: [] }
      scope.ui = { volume: 100 }
      scope.maxPercent = 150

      scope.$on('Fh1RadioState', function (event, data) {
        scope.$evalAsync(function () {
          scope.state = data || { running: false }
          scope.maxPercent = Math.round((data && data.maxVolume || 1.5) * 100)
          if (data && typeof data.volume === 'number') scope.ui.volume = Math.round(data.volume * 100)
        })
      })

      scope.tune = function (step) {
        bngApi.engineLua("extensions.hook('onFh1RadioTune', " + Number(step) + ")")
      }

      scope.setVolume = function () {
        bngApi.engineLua("extensions.hook('onFh1RadioSetVolume', " + (Number(scope.ui.volume) || 0) / 100 + ")")
      }

      bngApi.engineLua("extensions.hook('onFh1RadioRequestState')")
    }
  }
}])
