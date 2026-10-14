# Smell classifier

The classifier tells three kinds of air apart: **clean air**, **spicy** and **bad smell**.
On a board it has never seen before, it is right for about **4 sessions out of 5** (80% balanced accuracy;
guessing would give 33%).

| File | What it is |
|---|---|
| `smell_classifier.ipynb` | The training notebook, step by step |
| `smell_classifier.py` | The same code as a plain script |
| `smell_classifier_v0.2.json` | The trained model; upload it on the Classifier page |
| `dataset_windows.csv` | The prepared data: one row = 2 minutes of one session |
| `class_curves.png` | The average curve of each class |

## The model in a few sentences

Each BME688 sensor heats up to different temperatures (a heater profile) and measures its resistance at each step.
Different gases react at different temperatures, so the curve of resistances over the steps is a fingerprint of the air.

The model is a **logistic regression**. It takes 33 numbers that describe the shape of these curves (4 heater
profiles, 7 to 9 steps each), multiplies them by learned weights and adds them up into one score per class.
The scores are turned into probabilities that add up to 100%. If the best class is below 60%, the answer is
"unknown" instead of a guess. The whole model is 102 numbers.

## How we made it

1. **Data.** Boards 1, 2 and 4 measured about 150 objects, each once. From the experiment document we kept the objects
   that clearly fit one of three classes: 54 sessions (13 clean air, 30 spicy, 11 bad smell). Board 3 was kept
   for testing only.
2. **Warm-up removed.** The first 15 minutes of every session are left out: the sensors are still settling.
   This is the professor's "use the second measurement".
3. **Compared with clean air.** Every reading is compared with clean air from the same sensor (next section).
4. **Curve shape only.** From each curve we keep its shape, not its height. The shape says *which* smell;
   the height mostly says *how much*, and it also carries most of the sensor drift.
5. **2-minute windows.** The 2 sensors of each profile and all cycles in 2 minutes are averaged into one example.
   That gives 861 examples.
6. **Honest testing.** The model is trained on two boards and tested on the third, for each board in turn.
   That is the "unseen board" number. It's the one that matters, because the model will run on boards it wasn't
   trained on.

## Why we compare with clean air

The heater steps change the resistance much more than any smell does: between 100 °C and 320 °C the resistance
changes about 50 times, while a smell changes it 1.5 to 7 times, and only at some steps. So the raw curve is mostly
the sensor's own shape, with the smell as a smaller dent on top.

Every sensor has its own version of that shape (the chip, its age, the board). The difference between two sensors
in clean air is about as large as the dent a smell makes. Without a reference, the model partly learns *which board*
it is, not which smell. With the reference, each reading becomes "how different is this from clean air on this
sensor", the sensor's own shape cancels out and only the dent is left, and the dent looks similar on every board.

| Test | With clean air | Curve only |
|---|---|---|
| New session, same board | 79% | 83% |
| **A board it has never seen** | **80%** | 63% |

Like recognising a song through different speakers: each speaker colours the sound, so you first listen to each
speaker on its own and subtract its colour.

"Clean air" here means **the normal air where you measure, without the sample**: a "before" picture. It doesn't have
to be perfectly clean. It has to be the same place and conditions as the sample. Record it again after moving to
another room or outside, after a big change in temperature or humidity, or after a long time.

## What we tried

| Version | Unseen board |
|---|---|
| Curve shape + height (v0.1) | 73% |
| Clean air recorded closest in time, instead of one calibration per board | 50% |
| **Curve shape only, logistic regression (v0.2, current)** | **80%** |
| Curve only, no clean-air reference | 63% |
| Gradient boosting | 65% |
| LDA, SVM | 60–70% |
| kNN, per-board scaling, two-step model, combined models (on v0.1) | 52–75% |

**Other models and why they did worse:**
- **LDA** pictures each class as a cloud of points with the same shape and draws lines between the clouds.
  Our classes probably don't fit that picture: spicy objects vary a lot, clean air very little.
- **SVM** looks for the boundary with the widest gap between classes. It can bend the boundary, and with few
  sessions it likely bends around individual objects.
- **kNN** answers with the class of the most similar training examples. Small differences between boards can decide
  which examples are "most similar", so it tends to copy the board instead of the smell.
- **Gradient boosting** (the idea behind LightGBM) builds hundreds of small yes/no trees, each fixing the mistakes of the previous ones.
  It is very strong with thousands of examples. With 54 sessions it learns the noise.
- **Neural networks** can learn straight from the raw curves, but they need hundreds to thousands of sessions.

Logistic regression won because it is the simplest model: with little data, simple models carry over best to new
boards. The limit right now is the data, not the model.

## Results and limits

Unseen board: clean air 10 of 13, spicy 19 of 30, bad smell 11 of 11.
- Spicy is most often mistaken for clean air: many spicy objects (teas, syrups) smell weak.
- Bad smell has only 11 sessions, so its result is not reliable yet.
- Cinnamon at a distance (15 cm to 4 m, Test_1) is not recognised: training only had strong, close-up samples.
- Every object was measured once, on one board, without clean air right before it. Board 3 hasn't been
  tested properly yet (it ran different profiles).
- The settings were chosen after many tries, so 80% is a little optimistic. New recordings will tell.

## How to run it in Jupyter

In Terminal:

```bash
cd ~/Documents/ProiectDrone/clasificator
BME_ROOT=../masuratori ../.venv-ml/bin/jupyter lab
```

In the browser window that opens: double-click `smell_classifier.ipynb`, then **Run → Run All Cells**.
- A cell shows `[*]` while it runs and a number when it is done. The whole notebook takes a few minutes.
- The results appear under each cell: the accuracy table, the confusion matrix, the curves, the board 3 test.
- It writes `dataset_windows.csv`, `class_curves.png` and `smell_classifier_v0.2.json` into this folder.

First time only (from the `ProiectDrone` folder):

```bash
python3 -m venv .venv-ml
.venv-ml/bin/python -m pip install pandas numpy scikit-learn matplotlib jupyterlab
```

The data in `masuratori/` is not in git. On another computer, copy it from OneDrive first.

## How to take measurements

Set every board to the same heater profiles: 301 / 354 / 411 / 501 on sensors 1–2 / 3–4 / 5–6 / 7–8.

| Step | Time | What to do |
|---|---|---|
| Warm-up | 30 min, or the board is already running | Nothing near the board |
| Clean air | 5 min | The normal air at that place, without the sample |
| Sample | 10 min | Place the object at the chosen distance |
| Recovery | 5 min | Remove the object |

- Measure each object **2–3 times, on different days**, not once.
- When possible, put the same object in front of **several boards at the same time**.
- Mix the classes over the days; don't record all spicy objects on one day.
- Also record some samples further away (30 cm, 1 m), not only close up.
- Write down: date and time, board, object, amount, distance, room, who measured.

Goal for the next model: 10+ clean air, 15+ spicy and 15+ bad smell sessions, then a final test on board 3.
