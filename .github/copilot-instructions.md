# General

- If you want a clean run of EnvironmentData.py, make sure you clear out the data/ directory first. EnvironmentData will read data from that directory which can cause version/caching issues. 
- NEVER ADD DUMMY/ESTIMATED/DEFAULT VALUES to the data. If a field is not available in the raw data or calling script, it should not be included in the output, or an error should register.
- If you add printing for testing, remove it when you are done. Too much printing clutters the output and makes it hard to read.
- Use polars instead of pandas whenever possible. 
- If you are going to run python code, create a script to avoid syntax errors. Remove any test scripts you create. Make sure to activate the virtual environment at .venv before running python scripts; `& "C:\Users\super\Documents\savii\Analysis Tool\savii-data-scripts\.venv\Scripts\Activate.ps1"; python my_script.py`
- To escape `"`, use `""`, not `\"`.
- Only use functions if code is reused multiple times, or complex enough that it needs to be abstracted. Otherwise, keep it simple and keep code inline.


# If You Run PowerShell

- Try to avoid PowerShell in general and create temporary Python scripts instead.
- Do not use a pattern like `print(f'{county[\"name\"]}` or `\"service_area_id\"`. This will error out. Use ` print(f'{county[""name""]}` or `""service_area_id""` instead.
- Don't use `&&`, it will error out. Instead, use `;`. 
- To escape `"`, use `""`, not `\"`.
- I use Windows so don't use Linux commands.
- Don't forget to activate the virtual environment at .venv before running python scripts.
