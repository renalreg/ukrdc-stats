import marimo  # deptry: ignore - remove once PR to fix (linked in pyproject) is merged

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Dialysis patients prevalent for one year or more
    This notebook seeks to explore the prevelant dialysis patients who have been on dialysis modalities for at least a year.
    """)


@app.cell
def _():
    import marimo as mo  # deptry: ignore - remove once PR to fix (linked in pyproject) is merged

    return (mo,)


if __name__ == "__main__":
    app.run()
