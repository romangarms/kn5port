# FH1 conversion: working alongside other sessions

Several Claude Code sessions work in `fh1/` at once.

- **Build only through the queue**, never by running `build3.py` directly: `python buildq.py submit -m "<what you changed>" --by <short session name> --chunk <redrock|colorado|clearsprings|coloradodirt>`, from `fh1/pipeline`. `coloradodirt` is the Rally Expansion, whose data is in `fh1/dirt/` (docs/pipeline.md, Other tracks). Write the note for the other sessions: what changed and what to look for in-game.
- **Check `python buildq.py status` before building and before telling the user a build has their change.** A build includes only edits made before it started: compare its start time with your edits, or read its "files changed" list.
- To wait for your build, run `python buildq.py wait <request id>` in the background.
- Each chunk has its own build lane, so a Clear Springs build runs beside a whole-map one. Don't edit `fh1/build_snapshot_<chunk>/`: it's that lane's copy of `pipeline/` and is replaced each build.
- `--beamng-ok` only when BeamNG has a different level loaded: a build can't replace files of the level the game has open.
- See `docs/pipeline.md`, Build queue, for the details.
