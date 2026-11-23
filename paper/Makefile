# Rebuild even when the checkout already contains the released PDF.
.PHONY: paper
export SOURCE_DATE_EPOCH = 1795392000
export FORCE_SOURCE_DATE = 1
TEX = pdflatex -halt-on-error -interaction=batchmode -file-line-error -output-directory=build

paper:
	mkdir -p build
	$(TEX) article.tex || (cat build/article.log && exit 1)
	BIBINPUTS=.: bibtex build/article
	$(TEX) article.tex || (cat build/article.log && exit 1)
	$(TEX) article.tex || (cat build/article.log && exit 1)
	@if grep -E 'undefined references|Citation .* undefined|Rerun to get|Overfull .[hv]box' build/article.log; then exit 1; fi
	cp build/article.pdf build/article.release.pdf
	mv -f build/article.release.pdf article.pdf
