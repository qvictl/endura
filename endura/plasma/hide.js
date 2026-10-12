// Remove panel system-monitor widgets, returning a JSON snapshot to restore them.
function dump(w, path) {
    w.currentConfigGroup = path;
    var o = {keys: {}, groups: {}};
    w.configKeys.forEach(function (k) { o.keys[k] = w.readConfig(k, ""); });
    var subs = w.configGroups;
    subs.forEach(function (g) { o.groups[g] = dump(w, path.concat([g])); });
    return o;
}
var snap = {panels: []};
panels().forEach(function (p) {
    p.currentConfigGroup = ["General"];
    var entry = {id: p.id, order: p.readConfig("AppletOrder", ""), widgets: []};
    p.widgets().forEach(function (w) {
        if (w.type.indexOf("org.kde.plasma.systemmonitor") === 0) {
            entry.widgets.push({id: w.id, type: w.type, config: dump(w, [])});
        }
    });
    entry.widgets.forEach(function (s) {
        p.widgets().forEach(function (w) { if (w.id == s.id) w.remove(); });
    });
    if (entry.widgets.length) snap.panels.push(entry);
});
print(JSON.stringify(snap));
