#include "linux_hal.h"
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>
#include <linux/spi/spidev.h>

static int fd_spi = -1;
static int fd_i2c = -1;

static void set_cs(uint8_t sensor_idx, uint8_t state) {
    uint8_t buf[2] = {0x01, 0xFF};
    if (state == 0) {
        buf[1] = ~(1 << sensor_idx);
    }
    write(fd_i2c, buf, 2);
}

int8_t linux_hal_init(void) {
    if (fd_i2c >= 0) return 0;

    fd_i2c = open("/dev/i2c-1", O_RDWR);
    if (fd_i2c < 0) return -1;
    ioctl(fd_i2c, I2C_SLAVE, 0x20);

    uint8_t config[2] = {0x03, 0x00};
    write(fd_i2c, config, 2);
    
    uint8_t high[2] = {0x01, 0xFF};
    write(fd_i2c, high, 2);
    usleep(2000);

    fd_spi = open("/dev/spidev0.0", O_RDWR);
    if (fd_spi < 0) return -1;

    uint32_t speed = 5000000;
    uint8_t mode = 0;
    ioctl(fd_spi, SPI_IOC_WR_MODE, &mode);
    ioctl(fd_spi, SPI_IOC_WR_MAX_SPEED_HZ, &speed);

    // switch every sensor to SPI
    for (int i = 0; i < 8; i++) {
        uint8_t dummy_tx[2] = {0x73 | 0x80, 0x00};
        uint8_t dummy_rx[2] = {0};
        struct spi_ioc_transfer tr_dummy = {
            .tx_buf = (unsigned long)dummy_tx,
            .rx_buf = (unsigned long)dummy_rx,
            .len = 2,
        };
        set_cs(i, 0);
        ioctl(fd_spi, SPI_IOC_MESSAGE(1), &tr_dummy);
        set_cs(i, 1);
        usleep(2000);
    }

    return 0;
}

void linux_delay_us(uint32_t period, void *intf_ptr) {
    usleep(period);
}

BME68X_INTF_RET_TYPE linux_spi_read(uint8_t reg_addr, uint8_t *reg_data, uint32_t len, void *intf_ptr) {
    uint8_t sensor_idx = *(uint8_t*)intf_ptr;
    set_cs(sensor_idx, 0);
    
    uint8_t tx_addr = reg_addr | 0x80;
    struct spi_ioc_transfer tr[2] = {0};
    tr[0].tx_buf = (unsigned long)&tx_addr;
    tr[0].len = 1;
    tr[1].rx_buf = (unsigned long)reg_data;
    tr[1].len = len;

    ioctl(fd_spi, SPI_IOC_MESSAGE(2), tr);
    
    set_cs(sensor_idx, 1);
    return BME68X_OK;
}

BME68X_INTF_RET_TYPE linux_spi_write(uint8_t reg_addr, const uint8_t *reg_data, uint32_t len, void *intf_ptr) {
    uint8_t sensor_idx = *(uint8_t*)intf_ptr;
    set_cs(sensor_idx, 0);
    
    uint8_t tx_addr = reg_addr & 0x7F;
    struct spi_ioc_transfer tr[2] = {0};
    tr[0].tx_buf = (unsigned long)&tx_addr;
    tr[0].len = 1;
    tr[1].tx_buf = (unsigned long)reg_data;
    tr[1].len = len;

    ioctl(fd_spi, SPI_IOC_MESSAGE(2), tr);
    
    set_cs(sensor_idx, 1);
    return BME68X_OK;
}
