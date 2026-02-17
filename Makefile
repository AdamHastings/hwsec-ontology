TEX=main
PDF=$(TEX).pdf
BIB=$(TEX).bbl
REPRO_REPORT=artifacts/repro_report.txt
VOI_REPORT=artifacts/voi_priorities.csv
SENS_REPORT=artifacts/sensitivity_rankings.csv
CQ_REPORT=artifacts/cq_results.csv
LATEX=pdflatex
BIBTEX=bibtex
LATEXFLAGS=-interaction=nonstopmode -halt-on-error
SECTIONS=$(wildcard sections/*.tex)
PYTHON=python3
ARTIFACT_GEN=scripts/generate_cq_results.py

.PHONY: all pdf repro clean distclean

all: pdf

pdf: $(PDF)

$(PDF): $(TEX).tex $(SECTIONS) refs.bib
	$(LATEX) $(LATEXFLAGS) $(TEX).tex
	$(BIBTEX) $(TEX)
	$(LATEX) $(LATEXFLAGS) $(TEX).tex
	$(LATEX) $(LATEXFLAGS) $(TEX).tex

repro: artifacts/cost_tuples.csv artifacts/incident_tuples.csv artifacts/cq_queries.sparql ontology.ttl $(ARTIFACT_GEN)
	@set -e; \
	$(PYTHON) $(ARTIFACT_GEN) --cost artifacts/cost_tuples.csv --incidents artifacts/incident_tuples.csv --cq-out $(CQ_REPORT) --voi-out $(VOI_REPORT) --sensitivity-out $(SENS_REPORT); \
	tuples=$$(awk 'END {print NR - 1}' artifacts/cost_tuples.csv); \
	incident_rows=$$(awk 'END {print NR - 1}' artifacts/incident_tuples.csv); \
	cq_rows=$$(awk 'END {print NR - 1}' $(CQ_REPORT)); \
	families=$$(awk -F, 'NR > 1 {print $$2}' artifacts/cost_tuples.csv | sort -u | wc -l | tr -d ' '); \
	missing=$$(awk -F, 'NR > 1 {if ($$2=="" || $$5=="" || $$7=="" || $$8=="" || $$11=="" || $$12=="" || $$13=="" || $$14=="") m++} END {print m+0}' artifacts/cost_tuples.csv); \
	incident_missing=$$(awk -F, 'NR > 1 {if ($$2=="" || $$7=="" || $$8=="" || $$9=="" || $$10=="" || $$11=="" || $$12=="") m++} END {print m+0}' artifacts/incident_tuples.csv); \
	{ \
	  echo "Generated: $$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; \
	  echo "Tuples: $$tuples"; \
	  echo "Incident tuples: $$incident_rows"; \
	  echo "Families: $$families"; \
	  echo "CQ rows: $$cq_rows"; \
	  echo "Missing key fields: $$missing"; \
	  echo "Missing incident fields: $$incident_missing"; \
	  echo; \
	  echo "[Evidence grades]"; \
	  awk -F, 'NR > 1 {c[$$11]++} END {for (k in c) printf "%s,%d\n", k, c[k]}' artifacts/cost_tuples.csv | sort; \
	  echo; \
	  echo "[Data origins]"; \
	  awk -F, 'NR > 1 {c[$$12]++} END {for (k in c) printf "%s,%d\n", k, c[k]}' artifacts/cost_tuples.csv | sort; \
	  echo; \
	  echo "[Time horizons]"; \
	  awk -F, 'NR > 1 {c[$$7]++} END {for (k in c) printf "%s,%d\n", k, c[k]}' artifacts/cost_tuples.csv | sort; \
	  echo; \
	  echo "[Bearing modes]"; \
	  awk -F, 'NR > 1 {c[$$8]++} END {for (k in c) printf "%s,%d\n", k, c[k]}' artifacts/cost_tuples.csv | sort; \
	  echo; \
	  echo "[CQ status]"; \
	  awk -F, 'NR > 1 {c[$$2]++} END {for (k in c) printf "%s,%d\n", k, c[k]}' $(CQ_REPORT) | sort; \
	} > $(REPRO_REPORT); \
	test "$$missing" -eq 0; \
	test "$$incident_missing" -eq 0; \
	test "$$incident_rows" -gt 0
	@echo "Wrote $(REPRO_REPORT)"

clean:
	rm -f *.aux *.log *.out *.toc *.lof *.lot *.bbl *.blg *.fls *.fdb_latexmk *.synctex.gz *.acn *.acr *.alg *.glg *.glo *.gls *.ist *.run.xml


distclean: clean
	rm -f $(PDF)
