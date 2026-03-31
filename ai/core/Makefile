CXX      := g++
CXXFLAGS := -std=c++17 -O3 -march=native -ffast-math -fPIC -Wall -Wextra
TARGET   := ../libstatwise.so

all: $(TARGET)

$(TARGET): features.cpp features.h
	$(CXX) $(CXXFLAGS) -shared -o $(TARGET) features.cpp

clean:
	rm -f $(TARGET)

.PHONY: all clean
