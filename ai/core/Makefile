CXX      := g++
# NOTE: -march=native is deliberately NOT in the default flags. A .so built
# with it can SIGILL at import time on any CPU with fewer instruction sets
# than the build machine (CI runner vs. serving box vs. laptop). Opt in per
# machine with e.g. `make MARCH=native` or `MARCH=x86-64-v3 make`.
MARCH    ?=
CXXFLAGS := -fopenmp -std=c++17 -O3 $(if $(MARCH),-march=$(MARCH)) -ffast-math -fPIC -Wall -Wextra
TARGET   := ../libstatwise.so

all: $(TARGET)

$(TARGET): features.cpp features.h
	$(CXX) $(CXXFLAGS) -shared -o $(TARGET) features.cpp

clean:
	rm -f $(TARGET)

.PHONY: all clean
