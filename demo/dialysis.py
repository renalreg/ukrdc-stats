import marimo

__generated_with = "0.24.0"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # Dialysis patients prevalent for one year or more
    This notebook seeks to explore the prevelant dialysis patients who have been on dialysis modalities for at least a year.
    """)
    return


@app.cell
def _():
    import marimo as mo

    return (mo,)


if __name__ == "__main__":
    app.run()
