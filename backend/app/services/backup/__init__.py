"""Configuration backup and restore.

A backup is one encrypted file holding the server's configuration: users,
roles, projects, pipelines, secrets, channels, agents and settings — not build
history, logs, artifacts or registry images.

- ``format``    — the file: a readable header and a passphrase-encrypted body
- ``tables``    — which tables are configuration, and how a row is written
- ``export``    — read the configuration out of the database
- ``restore``   — make the database's configuration equal to a backup's
- ``store``     — the backups directory: names, listing, reading, writing
- ``settings``  — passphrase, schedule, remote storage and run state
- ``schedule``  — when a scheduled backup is due, and which ones to prune
- ``remote``    — the optional copy to S3-compatible storage
- ``service``   — the operations the API and the scheduled task call
"""
