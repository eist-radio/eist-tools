# Do stuff

```cmd
pip install -r requirements.txt

playwright install chromium
```

Log in to RadioCult in the browser and peep the available tracks:
https://api.radiocult.fm/api/station/eist-radio/media/track

Example run:

```cmd
scripts/add-eist-aris-shows.py "2025-12-08" --output-tracks
scripts/add-eist-aris-shows.py "2025-12-08" --output-schedule
scripts/add-eist-aris-shows.py "2025-12-08" --test-slots
scripts/add-eist-aris-shows.py "2025-12-08" --plan
scripts/add-eist-aris-shows.py "2025-12-08" --execute
```