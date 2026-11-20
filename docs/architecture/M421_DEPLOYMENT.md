# M421 Real deployment

Check out the tag on the target host and install the project in its virtual
environment. Provision live settings and credentials locally on that host.

The strategy lists select M421. From the checkout, run the offline preflight.

```sh
.venv/bin/python -B -m polymaker.m4_run --project-dir "$PWD" --mode live --check
```

Confirm the pool lists only `m421_combined_v1`. For an authorized live launch,
replace `--check` with `--live`. Keep host state and account ownership explicit.
