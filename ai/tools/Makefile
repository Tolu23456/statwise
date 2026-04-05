CXX      := g++
CXXFLAGS := -std=c++17 -O2 -march=native -Wall -Wextra -pthread
LDFLAGS  := -pthread

BINDIR   := ../../build
DATADIR  := ../data

.PHONY: all clean downloader cleaner dirs

all: dirs downloader cleaner

dirs:
	@mkdir -p $(BINDIR)

downloader: dataset_downloader.cpp
	@echo "[BUILD] Compiling dataset_downloader v3.0…"
	$(CXX) $(CXXFLAGS) -o $(BINDIR)/dataset_downloader dataset_downloader.cpp $(LDFLAGS)
	@echo "[BUILD] dataset_downloader → $(BINDIR)/dataset_downloader"

cleaner: dataset_cleaner.cpp
	@echo "[BUILD] Compiling dataset_cleaner v3.0…"
	$(CXX) $(CXXFLAGS) -o $(BINDIR)/dataset_cleaner dataset_cleaner.cpp $(LDFLAGS)
	@echo "[BUILD] dataset_cleaner → $(BINDIR)/dataset_cleaner"

clean:
	rm -f $(BINDIR)/dataset_downloader $(BINDIR)/dataset_cleaner

run-downloader: downloader
	$(BINDIR)/dataset_downloader $(DATADIR)/raw

run-cleaner: cleaner
	$(BINDIR)/dataset_cleaner $(DATADIR)/raw $(DATADIR)/clean

run-all: downloader cleaner
	$(BINDIR)/dataset_downloader $(DATADIR)/raw
	$(BINDIR)/dataset_cleaner    $(DATADIR)/raw $(DATADIR)/clean
