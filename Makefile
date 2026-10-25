# Age of Confidence. Run "make help" for the list of commands.
PYTHON ?= python

.PHONY: help install test lint check figures note scripts schema clean

help:
	@echo "make install   install the package with the tools for tests and figures"
	@echo "make check     style check, then tests"
	@echo "make test      tests only"
	@echo "make lint      style check only"
	@echo "make figures   draw every figure again"
	@echo "make note      compile the theory note (needs pdflatex)"
	@echo "make scripts   build the workstation scripts of artery-ext/scripts"
	@echo "make schema    write docs/observations_schema.md"
	@echo "make clean     remove caches and LaTeX side files"

install:
	$(PYTHON) -m pip install -e ".[dev,figures]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

check: lint test

# The figures of the theory note, then one script per figure of the papers (figures/f*.py).
figures:
	$(PYTHON) theory/note/code/a5_figures.py
	@for script in $(wildcard figures/f*.py); do echo "$$script"; $(PYTHON) "$$script" || exit 1; done

note:
	cd theory/note && pdflatex -interaction=nonstopmode -halt-on-error aoc_theory_note.tex > /dev/null \
		&& pdflatex -interaction=nonstopmode -halt-on-error aoc_theory_note.tex > /dev/null
	@echo "written: theory/note/aoc_theory_note.pdf"

scripts:
	$(PYTHON) artery-ext/tools/build_scripts.py

schema:
	$(PYTHON) -m aoc.io.schema > docs/observations_schema.md

clean:
	rm -rf .pytest_cache .ruff_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	cd theory/note && rm -f *.aux *.log *.out *.toc *.bbl *.blg *.fls *.fdb_latexmk *.synctex.gz
