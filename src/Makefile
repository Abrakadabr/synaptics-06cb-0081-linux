# src/Makefile
# Cross-compiles the Wine UMDF biometric harness binaries for Synaptics 06cb:0081

CXX ?= x86_64-w64-mingw32-g++
DLLTOOL ?= x86_64-w64-mingw32-dlltool

WDK_INCLUDE ?= /usr/include/wdf/umdf/1.11
WINE_LIB ?= /usr/lib/x86_64-linux-gnu/wine

CXXFLAGS += -Wall -O2 -Wl,--stack,32000000
INCLUDES += -I$(WDK_INCLUDE) -I.
LIBS += -luuid -lole32 -L. -lpropsys

all: a.exe

libpropsys.a:
	@if [ -f $(WINE_LIB)/libpropsys.def ]; then \
		$(DLLTOOL) -k -d $(WINE_LIB)/libpropsys.def -l libpropsys.a; \
	elif [ -f /usr/share/wine/libpropsys.def ]; then \
		$(DLLTOOL) -k -d /usr/share/wine/libpropsys.def -l libpropsys.a; \
	else \
		echo "Note: libpropsys.def not found in standard wine paths. Using pre-existing libpropsys.a if present."; \
	fi

a.exe: hello.cc breakpoints.c breakpoints.h libpropsys.a
	$(CXX) $(CXXFLAGS) $(INCLUDES) hello.cc breakpoints.c $(LIBS) -o $@

clean:
	rm -f a.exe libpropsys.a

.PHONY: all clean
