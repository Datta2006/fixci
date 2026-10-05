# FixCI demo — bad dependency pin

`flask==0.0.1` does not exist, so `pip install -r requirements.txt` fails.
FixCI should classify this as a **dependency** failure and propose a valid pin.
