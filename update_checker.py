name: Bihar EduAlert Automatic Updates

on:
  workflow_dispatch:

  schedule:
    - cron: "0 */6 * * *"

permissions:
  contents: read

jobs:
  check-updates:
    runs-on: ubuntu-24.04

    steps:
      - name: Download project
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install libraries
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Check official updates and upload to Firebase
        env:
          FIREBASE_SERVICE_ACCOUNT: ${{ secrets.FIREBASE_SERVICE_ACCOUNT }}
        run: python update_checker.py

      - name: Automatically extract and update MCQs
        env:
          FIREBASE_SERVICE_ACCOUNT: ${{ secrets.FIREBASE_SERVICE_ACCOUNT }}
        run: python mcq_extractor.py

      - name: Save update file
        uses: actions/upload-artifact@v4
        with:
          name: Bihar-CBSE-Updates
          path: updates.json
          if-no-files-found: warn
