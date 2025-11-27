# Do stuff

```cmd
pip install -r requirements.txt

playwright install chromium
```

Log in to RadioCult in the browser and peep the available tracks:
https://api.radiocult.fm/api/station/eist-radio/media/track

Steps:

```cmd
# Downloads the current schedule for the week and saves it to schedule.json
scripts/add-eist-aris-shows.py "2025-12-08" --output-schedule
# Fetches eligible replay shows from past 3 weeks and saves their metadata to tracks.json
scripts/add-eist-aris-shows.py "2025-12-08" --output-tracks
# Finds all empty time slots in the schedule and saves them to empty-slots.json
scripts/add-eist-aris-shows.py "2025-12-08" --test-slots
# Randomly maps shows from tracks.json to slots from empty-slots.json and saves to updated-slots.json
scripts/add-eist-aris-shows.py "2025-12-08" --plan
# Automatically creates all shows using the mappings from updated-slots.json via browser automation
scripts/add-eist-aris-shows.py "2025-12-08" --execute
```