# Model files

Present (from the deployment bundle):

    config.json        max_length 256, chunk_chars 1500, chunk_overlap 200
    label_map.json     29 BIO labels over 14 PII categories
    tokenizer/         tokenizer.json + tokenizer_config.json

**Missing: `pii_detector_int8.onnx`.**

The bundle's own README lists it, but it was not in the ZIP. Drop it in this
directory and restart the server — `server/privacy/ner.py` picks it up with no
code change, and `GET /health` flips `ner_available` to `true`.

Without it the firewall runs on deterministic rules plus HTML structure. That
covers emails, phones, cards (Luhn), IPs, Aadhaar (Verhoeff), PAN, IFSC, dates
of birth, API keys, and every credential field with structural evidence. It does
not cover contextual detections with no pattern and no markup signal — a bare
person name in body text being the main one.

The tokenizer is loaded with `tokenizers`, not `transformers`. Both read the
same `tokenizer.json`; the lighter one keeps roughly a gigabyte of dependency
out of a package that is meant to run on-device.

Override the location with `PRIVYAGENT_MODEL_DIR=/path/to/models`.
