Unit tests use synthetic records and do not validate national EPA data. From the repository root:

Windows PowerShell: `$env:PYTHONPATH = "scripts"; py -3 -m unittest discover -s tests -v`

Unix: `PYTHONPATH=scripts python3 -m unittest discover -s tests -v`
