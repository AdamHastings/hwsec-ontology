TEX=main
PDF=$(TEX).pdf
BIB=$(TEX).bbl
LATEX=pdflatex
BIBTEX=bibtex
LATEXFLAGS=-interaction=nonstopmode -halt-on-error
SECTIONS=$(wildcard sections/*.tex)

.PHONY: all pdf clean distclean

all: pdf

pdf: $(PDF)

$(PDF): $(TEX).tex $(SECTIONS) refs.bib
	$(LATEX) $(LATEXFLAGS) $(TEX).tex
	$(BIBTEX) $(TEX)
	$(LATEX) $(LATEXFLAGS) $(TEX).tex
	$(LATEX) $(LATEXFLAGS) $(TEX).tex

clean:
	rm -f *.aux *.log *.out *.toc *.lof *.lot *.bbl *.blg *.fls *.fdb_latexmk *.synctex.gz *.acn *.acr *.alg *.glg *.glo *.gls *.ist *.run.xml


distclean: clean
	rm -f $(PDF)
