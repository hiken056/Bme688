# Classifier profile requirements

The classifier UI currently stores model metadata in the browser. It does not load
trained weights or run inference yet. The local presentation uses simulated predictions.

Models declare the exact profiles and the number of sensors needed for each.
Physical sensor positions are not fixed. Four profiles with two sensors each can
be assigned in any order. A model using fewer than eight sensors can ignore the others.

Example JSON metadata for a model using two standard-profile inputs:

```json
{
  "name": "Odor groups",
  "version": "1.0",
  "classes": ["Clean air", "Food", "Trash"],
  "window_seconds": 60,
  "warmup_seconds": 120,
  "prediction_interval_seconds": 5,
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
}
```

Add more entries for other profiles, up to eight model inputs in total.
Use the settings saved during training, not settings copied from a later device configuration.
Ticks use the acquisition program's 140 ms units. Step order matters.

Validation compares temperature steps, timing, mode, duty and sleep. Profile names
are labels; renaming an otherwise identical profile does not make it incompatible.
Missing settings, too few matching sensors or unavailable device configuration
block predictions, but do not block model selection or measurement recording.

Selecting a model does not change sensor settings. Acquisition checks compatibility
before starting. Matching settings show the ready message; a mismatch offers
Use model profiles or manual configuration. The explicit apply action previews
changed assignments and requires confirmation. It keeps matching assignments,
unrelated settings and measurement files, and never starts recording.
It saves locally in the presentation and uses the existing config API on a device.

The compatibility check also produces an input-order mapping. When inference is
implemented, the backend must validate again and use that mapping before building
the model's feature vector. Switching profiles must discard any incomplete analysis window.
Duplicate inputs are matched in ascending physical sensor order; a future model
must be trained for interchangeable sensors or aggregate them within each profile.
Matching profiles alone does not prove equal sensitivity or accuracy across devices.
