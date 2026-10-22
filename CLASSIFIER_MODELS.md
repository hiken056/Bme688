# Classifier on the device

The Pi stores the models and makes the predictions. The interface only uploads models and shows the results.
Predictions continue when no phone is connected.

| What | Where on the Pi |
|---|---|
| Models | `/var/lib/bme688/models/` |
| Selected model | `/var/lib/bme688/active_model.json` |
| Predictions | `predictions.csv` next to the sensor files of each recording |

The code is in `backend/classifier.py`. It uses plain Python only, so nothing extra has to be installed on the Pi.

## Model file

Export it with the notebook (`clasificator/smell_classifier.ipynb`, section 7). It has three parts:

- `metadata`: name, version, classes, timings and `profile_requirements` (below);
- `model`: the feature names, the scaling values, the weights (`coef`, `intercept`) and `unknown_below`;
- `clean_air_reference`: the clean air of each training board, which the Pi uses as the reference.

Only multinomial logistic regression on curve-shape features (`<profile>_shape_s<step>`) is supported.
The upload is refused, with the reason, when the file is not valid JSON, a section is missing, the numbers don't match
the feature list, the classes differ between `metadata` and `model`, or a feature can't be computed on the device.
The rows of `coef` follow `model.classes` (scikit-learn sorts them alphabetically). `metadata.classes` sets only the
order shown in the interface.

Models can't be added, selected or removed while a measurement is running.

## Profile requirements

A model declares the exact heater profiles and how many sensors run each one:

```json
"profile_requirements": [
  {
    "profile": "hp_354_standard",
    "count": 2,
    "mode": "parallel",
    "duty": 0,
    "sleep": 0,
    "temps": [320, 100, 100, 100, 200, 200, 200, 320, 320, 320],
    "ticks": [5, 2, 10, 30, 5, 5, 5, 5, 5, 5]
  }
]
```

Use the settings from training, not ones copied from a later configuration. Ticks are in the acquisition
program's 140 ms units, and step order matters.

The check compares temperatures, ticks, mode, duty and sleep. Profile names are labels only. Sensor positions are
free: the sensors are matched by settings, in ascending sensor order, by both the interface and the Pi. On a mismatch,
the panel offers **Use model profiles**, which shows the changes and asks for confirmation before saving the configuration.

## Test and collect data

The Acquisition page has a switch above the cycles: **Test** or **Collect data**. The choice and the number of cycles
are saved on the Pi (`guide_settings.json`), so they are the same on every device and after a reboot.
The Pi runs both, the page only shows them, so closing the phone changes nothing. The code is in `backend/guide.py`.

**Collect data** (labelled samples for training):
1. Warm-up, decided from the data (below).
2. 3 min clean air: the reference for this cycle, saved as `reference_cycleN.json`.
3. "Add the sample": sound, a bar at the bottom of the page, tap **Sample placed**.
4. 10 min with the sample. Predictions start 2 min after the tap and use only data from after the tap.
5. "Remove the sample", tap **Sample removed**, 3 min airing out, then the next cycle from step 2.
   After the last sample the Pi stops the measurement on its own.

Every prediction is saved with the sample and its class (`Sample`, `Expected` in `predictions.csv`), so the accuracy
can be computed from the collected data. `cycles.json` keeps the form and when each sample was placed and removed.

**Test** (anything, unlabelled): warm-up, 3 min clean air, then predictions until Stop.
**New clean air** takes a new reference, for the next thing or after moving. Tests are saved in the `Tests` folder
and are not used for training.

**Warm-up**: every 30 s the Pi compares the last 1.5 min of each sensor with the 1.5 min before. The typical change of
ln R over the heater steps, per minute, is the drift; the worst sensor counts. Warm when the drift is below 0.05 per
minute 3 times in a row (0.05 in ln R is about 5% change in resistance). It needs 3 min of data first and never
waits more than 20 min. If it hits that limit, it goes on anyway: `capped` in `cycles.json`, a pop-up on the page, and
a warning with every prediction of that measurement. The bar on the page fills as the checks pass. On the recordings so far: 4 to 5.5 min when the board was already warm, 7.5 to 12.5 min
after a cold start.

If the Pi reboots during a measurement, `cycles.json` of that recording gets `"interrupted": true`.
A measurement started without the switch (older interface) still uses minutes 10-15 as clean air.

## Checks before each prediction

| Check | If it fails |
|---|---|
| Heater profiles match the model | No predictions; "Use model profiles" |
| Sensor data arriving | "No sensor data" |
| Training clean air for these profiles | "No clean-air reference" |
| Enough heater cycles in the window (at least half of the expected number) and at most 25% missing values | "Not enough data" |
| Pattern similar to the training data (fewer than 3 values more than 5 standard deviations away; no training window had any beyond 4) | "Unusual pattern", no class |
| Top probability at least `unknown_below` (60%) | "Unknown" with the closest class |

## Tests

```bash
python -m unittest tests.test_classifier_device        # model checks, prediction states
BME_ROOT=masuratori python -m unittest tests.test_classifier_parity   # same numbers as the notebook (needs .venv-ml and the data)
node tests/test_classifier_profiles.mjs                 # profile matching in the interface
```

The parity test runs the notebook's own functions and the Pi code on recorded sessions and requires every feature to
agree to 9 decimals and every probability to match scikit-learn.
