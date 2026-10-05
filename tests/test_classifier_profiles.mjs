import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function storage() {
    const values = new Map();
    return {
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value)
    };
}

function page(search = '?demo=1', fetch = async () => { throw new Error('Offline'); }) {
    const window = new EventTarget();
    window.location = { search };
    const context = vm.createContext({
        window, document: { addEventListener() {} }, URLSearchParams, Event,
        localStorage: storage(), sessionStorage: storage(), fetch,
        AbortController, setTimeout, clearTimeout
    });
    for (const file of ['heater-profiles.js', 'classifier-store.js']) {
        vm.runInContext(readFileSync(new URL(`../interface/js/${file}`, import.meta.url), 'utf8'), context);
    }
    return { api: window.ClassifierPreview, context };
}

const clone = value => JSON.parse(JSON.stringify(value));
const { api } = page();
assert.equal(api.read().models.length, 12);
assert.equal(new Set(api.read().models.map(model => model.id)).size, 12);
assert.ok(api.read().models.every(model => api.validRequirements(model)));
await api.refreshConfiguration();
const model = api.activeModel();
const original = clone(api.configuration().config);
assert.equal(api.compatibility(model).compatible, true);
assert.equal(api.profileGroups(model).length, 4);
const countBased = api.expandRequirements(api.profileGroups(model).map(group => ({
    ...clone(group.settings), count: group.sensors.length
})));
assert.equal(api.compatibility(countBased).compatible, true);
assert.equal(countBased.sensors.length, 8);
for (const requirements of [[], [{ ...clone(model.sensor_profiles[1]), count: 9 }],
    [{ ...clone(model.sensor_profiles[1]), count: 1.5 }],
    [{ profile: 'hp_301_basic', count: 2 }]]) {
    assert.throws(() => api.expandRequirements(requirements));
}

function checkChange(change, expected) {
    const config = clone(original);
    change(config);
    const check = api.compatibility(model, { status: 'ready', config });
    assert.equal(check.status, 'mismatch');
    assert.ok(check.issues.some(issue => issue.includes(expected)), JSON.stringify(check));
}

const sensorOne = config => config.presets.find(preset => preset.id === config.assignments[1]);
checkChange(config => { sensorOne(config).temps[0]++; }, 'hp_301_basic');
checkChange(config => { sensorOne(config).ticks[0]++; }, 'hp_301_basic');
checkChange(config => { sensorOne(config).mode = 'sequential'; }, 'hp_301_basic');
checkChange(config => { sensorOne(config).duty = 2; }, 'hp_301_basic');
checkChange(config => { sensorOne(config).sleep = 5; }, 'hp_301_basic');
checkChange(config => { config.assignments[1] = config.assignments[3]; }, 'needs 2 sensors, found 1');
checkChange(config => { delete config.assignments[1]; }, 'needs 2 sensors, found 1');
checkChange(config => { config.presets.push(clone(sensorOne(config))); }, 'needs 2 sensors, found 0');

const reordered = clone(original);
for (let sensor = 1; sensor <= 8; sensor++) reordered.assignments[sensor] = original.assignments[9 - sensor];
const reorderedCheck = api.compatibility(model, { status: 'ready', config: reordered });
assert.equal(reorderedCheck.compatible, true);
assert.equal(reorderedCheck.input_mapping[1], 7);
assert.equal(new Set(Object.values(reorderedCheck.input_mapping)).size, 8);

const renamed = clone(original);
sensorOne(renamed).name = 'Renamed but identical';
assert.equal(api.compatibility(model, { status: 'ready', config: renamed }).compatible, true);
const legacy = clone(original);
for (const preset of legacy.presets) { preset.sleep_sec = preset.sleep; delete preset.sleep; }
assert.equal(api.compatibility(model, { status: 'ready', config: legacy }).compatible, true);

const subset = clone(model);
subset.sensors = [3, 4];
subset.profiles = ['hp_354_standard'];
subset.sensor_profiles = { 3: subset.sensor_profiles[3], 4: subset.sensor_profiles[4] };
const otherSensors = clone(original);
sensorOne(otherSensors).temps[0]++;
assert.equal(api.compatibility(subset, { status: 'ready', config: otherSensors }).compatible, true);
assert.equal(api.compatibility(subset, { status: 'ready', config: reordered }).compatible, true);

for (const change of [
    value => { delete value.sensor_profiles; },
    value => { delete value.sensor_profiles[1]; },
    value => { value.sensor_profiles[9] = clone(value.sensor_profiles[1]); },
    value => { value.sensor_profiles[1].temps[0] = '100'; },
    value => { value.sensor_profiles[1].ticks.pop(); },
    value => { value.sensor_profiles[1].mode = 'forced'; },
    value => { value.sensor_profiles[1].sleep = -1; },
    value => { value.sensor_profiles[1].duty = 0.5; },
    value => { value.sensors.push(1); }
]) {
    const invalid = clone(model);
    change(invalid);
    assert.equal(api.compatibility(invalid).status, 'unverified');
}
assert.equal(api.compatibility(model, { status: 'checking', config: null }).compatible, false);
assert.equal(api.compatibility(model, { status: 'unavailable', config: original }).compatible, false);

const unchanged = JSON.stringify(original);
const noChange = api.configurationForModel(model, original);
assert.equal(noChange.changes.length, 0);
assert.equal(JSON.stringify(original), unchanged);
const wrongAssignment = clone(original);
wrongAssignment.assignments[1] = 'heater_354';
wrongAssignment.extra_setting = 'keep';
wrongAssignment.file_duration_minutes = 15;
const repaired = api.configurationForModel(model, wrongAssignment);
assert.equal(repaired.changes.length, 1);
assert.equal(repaired.changes[0].from, 'hp_354_standard');
assert.equal(repaired.changes[0].to, 'hp_301_basic');
assert.equal(repaired.config.extra_setting, 'keep');
assert.equal(repaired.config.file_duration_minutes, 15);
assert.equal(api.compatibility(model, { status: 'ready', config: repaired.config }).compatible, true);
for (let sensor = 1; sensor <= 8; sensor++) {
    if (sensor !== repaired.changes[0].sensor) {
        assert.equal(repaired.config.assignments[sensor], wrongAssignment.assignments[sensor]);
    }
}
const allStandard = clone(original);
allStandard.assignments = Object.fromEntries(Array.from({ length: 8 }, (_, index) => [index + 1, 'heater_354']));
const rearranged = api.configurationForModel(model, allStandard);
assert.equal(rearranged.changes.length, 6);
assert.equal(rearranged.config.assignments[1], 'heater_354');
assert.equal(rearranged.config.assignments[2], 'heater_354');
assert.equal(api.compatibility(model, { status: 'ready', config: rearranged.config }).compatible, true);
const custom = clone(subset);
custom.sensor_profiles[3].temps[0] = 310;
custom.sensor_profiles[4].temps[0] = 310;
const customPlan = api.configurationForModel(custom, original);
assert.equal(customPlan.config.presets.length, original.presets.length + 1);
assert.equal(customPlan.changes.length, 2);
assert.equal(customPlan.config.presets.at(-1).builtin, false);
assert.equal(api.compatibility(custom, { status: 'ready', config: customPlan.config }).compatible, true);
assert.equal(JSON.stringify(original), unchanged);
assert.throws(() => api.configurationForModel({}, original));

api.saveDemoConfiguration(otherSensors);
await api.refreshConfiguration();
assert.equal(api.compatibility(model).status, 'mismatch');

const offline = page('');
offline.context.localStorage.setItem('bmeConfig', JSON.stringify(original));
await offline.api.refreshConfiguration();
assert.equal(offline.api.configuration().status, 'unavailable');
assert.equal(offline.api.compatibility(model).compatible, false);

const live = page('', async () => ({ ok: true, json: async () => clone(original) }));
await live.api.refreshConfiguration();
assert.equal(live.api.compatibility(model).compatible, true);
assert.equal(live.api.read().models.length, 0);
console.log('PASS: exact profile settings and counts, reordered sensor groups with unique input mapping, renamed profiles, subsets, invalid metadata, saved demo config and fail-closed live config.');
