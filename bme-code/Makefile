CC = gcc
CFLAGS = -Wall -I. -IBME68x-Sensor-API
# raw BME68x API; BSEC is not linked
LDFLAGS = -lm -lrt -pthread

SRCS = main.c linux_hal.c BME68x-Sensor-API/bme68x.c
OBJS = $(SRCS:.c=.o)
TARGET = main

all: $(TARGET)

.PHONY: all clean

$(TARGET): $(OBJS)
	$(CC) -o $@ $^ $(LDFLAGS)

clean:
	rm -f $(OBJS) $(TARGET)
