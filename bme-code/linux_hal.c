#include "linux_hal.h"
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>
#include <linux/spi/spidev.h>

static int fd_spi = -1;
static int fd_i2c = -1;

// failed bus calls since the last check, read by the main loop
static unsigned long bus_errors = 0;

static void count_error(void) {
    __atomic_fetch_add(&bus_errors, 1, __ATOMIC_RELAXED);
}

unsigned long linux_hal_take_errors(void) {
    return __atomic_exchange_n(&bus_errors, 0, __ATOMIC_RELAXED);
}

// the chip select of each sensor is a pin on the i2c expander
static int set_cs(uint8_t sensor_idx, uint8_t state) {
    uint8_t buf[2] = {0x01, 0xFF};
    if (state == 0) {
        buf[1] = ~(1 << sensor_idx);
    }
    return write(fd_i2c, buf, 2) == 2 ? 0 : -1;
}

int8_t linux_hal_init(void) {
    if (fd_i2c >= 0) return 0;

    fd_i2c = open("/dev/i2c-1", O_RDWR);
    if (fd_i2c < 0) return HAL_NO_I2C;
    if (ioctl(fd_i2c, I2C_SLAVE, 0x20) < 0) return HAL_NO_BOARD;

    // the expander answers only when the sensor board is connected
    uint8_t config[2] = {0x03, 0x00};
    if (write(fd_i2c, config, 2) != 2) return HAL_NO_BOARD;

    uint8_t high[2] = {0x01, 0xFF};
    if (write(fd_i2c, high, 2) != 2) return HAL_NO_BOARD;
    usleep(2000);

    fd_spi = open("/dev/spidev0.0", O_RDWR);
    if (fd_spi < 0) return HAL_NO_SPI;

    uint32_t speed = 5000000;
    uint8_t mode = 0;
    if (ioctl(fd_spi, SPI_IOC_WR_MODE, &mode) < 0 ||
        ioctl(fd_spi, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) return HAL_NO_SPI;

    // switch every sensor to spi
    for (int i = 0; i < 8; i++) {
        uint8_t dummy_tx[2] = {0x73 | 0x80, 0x00};
        uint8_t dummy_rx[2] = {0};
        struct spi_ioc_transfer tr_dummy = {
            .tx_buf = (unsigned long)dummy_tx,
            .rx_buf = (unsigned long)dummy_rx,
            .len = 2,
        };
        if (set_cs(i, 0) != 0) return HAL_NO_BOARD;
        if (ioctl(fd_spi, SPI_IOC_MESSAGE(1), &tr_dummy) < 0) return HAL_NO_SPI;
        if (set_cs(i, 1) != 0) return HAL_NO_BOARD;
        usleep(2000);
    }

    return 0;
}

void linux_delay_us(uint32_t period, void *intf_ptr) {
    usleep(period);
}

// a failed call is counted and reported to the bosch api, never ignored
static BME68X_INTF_RET_TYPE transfer(uint8_t sensor_idx, struct spi_ioc_transfer tr[2]) {
    if (set_cs(sensor_idx, 0) != 0) {
        count_error();
        set_cs(sensor_idx, 1);
        return BME68X_E_COM_FAIL;
    }
    int ok = ioctl(fd_spi, SPI_IOC_MESSAGE(2), tr) >= 0;
    if (set_cs(sensor_idx, 1) != 0) ok = 0;
    if (!ok) {
        count_error();
        return BME68X_E_COM_FAIL;
    }
    return BME68X_OK;
}

BME68X_INTF_RET_TYPE linux_spi_read(uint8_t reg_addr, uint8_t *reg_data, uint32_t len, void *intf_ptr) {
    uint8_t sensor_idx = *(uint8_t*)intf_ptr;
    uint8_t tx_addr = reg_addr | 0x80;
    struct spi_ioc_transfer tr[2] = {0};
    tr[0].tx_buf = (unsigned long)&tx_addr;
    tr[0].len = 1;
    tr[1].rx_buf = (unsigned long)reg_data;
    tr[1].len = len;
    return transfer(sensor_idx, tr);
}

BME68X_INTF_RET_TYPE linux_spi_write(uint8_t reg_addr, const uint8_t *reg_data, uint32_t len, void *intf_ptr) {
    uint8_t sensor_idx = *(uint8_t*)intf_ptr;
    uint8_t tx_addr = reg_addr & 0x7F;
    struct spi_ioc_transfer tr[2] = {0};
    tr[0].tx_buf = (unsigned long)&tx_addr;
    tr[0].len = 1;
    tr[1].tx_buf = (unsigned long)reg_data;
    tr[1].len = len;
    return transfer(sensor_idx, tr);
}
