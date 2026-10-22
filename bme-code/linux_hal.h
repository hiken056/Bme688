#ifndef LINUX_HAL_H
#define LINUX_HAL_H

#include <stdint.h>
#include "BME68x-Sensor-API/bme68x_defs.h"

// what linux_hal_init can report
#define HAL_NO_I2C   -1
#define HAL_NO_BOARD -2
#define HAL_NO_SPI   -3

int8_t linux_hal_init(void);
unsigned long linux_hal_take_errors(void);
void linux_delay_us(uint32_t period, void *intf_ptr);
BME68X_INTF_RET_TYPE linux_spi_read(uint8_t reg_addr, uint8_t *reg_data, uint32_t len, void *intf_ptr);
BME68X_INTF_RET_TYPE linux_spi_write(uint8_t reg_addr, const uint8_t *reg_data, uint32_t len, void *intf_ptr);

#endif
