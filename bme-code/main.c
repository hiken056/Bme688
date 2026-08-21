#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <time.h>
#include <pthread.h>
#include "bme68x.h"
#include "linux_hal.h"

#define NUM_SENSORS      8
#define NUM_STEPS        10
#define MEASUREMENTS_DIR "measurements"
#define CONFIG_PATH      "/var/lib/bme688/config.json"
#define LEGACY_CONFIG_PATH "/tmp/bme_config.json"
#define LIVE_PATH        "/tmp/bme_latest.json"
#define PROFILE_TICK_MS  140
#define MAX_SEQUENTIAL_TICKS 28

// new data, valid gas reading, and stable heater
#define BME68X_VALID_DATA  UINT8_C(0xB0)

typedef struct {
    char     name[64];
    char     mode[16];
    int      duty;
    int      sleep_sec;
    uint16_t temps[NUM_STEPS];
    uint16_t ticks[NUM_STEPS];
    int      loaded;
} SensorProfile;

typedef struct {
    float temperature, humidity, pressure, gas_resistance;
    int   gas_index, valid;
    char  preset_name[64];
} LiveReading;

static FILE *g_cycle_files[NUM_SENSORS];
static volatile int g_running = 1;
static LiveReading live[NUM_SENSORS];

// shared thread locks
static pthread_mutex_t spi_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t live_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t log_mutex = PTHREAD_MUTEX_INITIALIZER;

static void handle_shutdown(int sig) {
    g_running = 0;
    pthread_mutex_lock(&log_mutex);
    printf("\n[SHUTDOWN] Signal %d received. Flushing and closing all files...\n", sig);
    for (int i = 0; i < NUM_SENSORS; i++) {
        if (g_cycle_files[i]) {
            fflush(g_cycle_files[i]);
            fclose(g_cycle_files[i]);
            g_cycle_files[i] = NULL;
            printf("[SHUTDOWN] Sensor %d file closed safely.\n", i + 1);
        }
    }
    printf("[SHUTDOWN] All data saved. Exiting cleanly.\n");
    pthread_mutex_unlock(&log_mutex);
    exit(0);
}

// locked SPI access
static int8_t locked_spi_read(uint8_t reg_addr, uint8_t *reg_data, uint32_t len, void *intf_ptr) {
    int8_t rslt;
    pthread_mutex_lock(&spi_mutex);
    rslt = linux_spi_read(reg_addr, reg_data, len, intf_ptr);
    pthread_mutex_unlock(&spi_mutex);
    return rslt;
}

static int8_t locked_spi_write(uint8_t reg_addr, const uint8_t *reg_data, uint32_t len, void *intf_ptr) {
    int8_t rslt;
    pthread_mutex_lock(&spi_mutex);
    rslt = linux_spi_write(reg_addr, reg_data, len, intf_ptr);
    pthread_mutex_unlock(&spi_mutex);
    return rslt;
}

static void my_delay_us(uint32_t period, void *intf_ptr) { usleep(period); }

// config parser
static const char *json_find_key(const char *hay, const char *key) {
    char pattern[128];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(hay, pattern);
    if (!p) return NULL;
    p += strlen(pattern);
    while (*p == ' ' || *p == ':' || *p == '\t') p++;
    return p;
}

static int json_parse_int(const char *p) {
    while (*p == ' ' || *p == '\t') p++;
    return atoi(p);
}

static void json_parse_string(const char *p, char *buf, size_t buf_len) {
    while (*p && *p != '"') p++;
    if (!*p) { buf[0] = '\0'; return; }
    p++;
    size_t i = 0;
    while (*p && *p != '"' && i < buf_len - 1) buf[i++] = *p++;
    buf[i] = '\0';
}

static void json_parse_int_array(const char *p, uint16_t *out, int len) {
    while (*p && *p != '[') p++;
    if (!*p) return;
    p++;
    for (int i = 0; i < len; i++) {
        while (*p == ' ' || *p == ',') p++;
        if (!*p || *p == ']') break;
        out[i] = (uint16_t)atoi(p);
        while (*p && *p != ',' && *p != ']') p++;
    }
}

static const char *json_find_key_bounded(const char *hay, const char *end, const char *key) {
    char pattern[128];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    size_t plen = strlen(pattern);
    const char *p = hay;
    while (p < end) {
        p = strstr(p, pattern);
        if (!p || p >= end) return NULL;
        p += plen;
        while (p < end && (*p == ' ' || *p == ':' || *p == '\t')) p++;
        return p;
    }
    return NULL;
}

static const char *find_object_end(const char *start) {
    int depth = 0;
    const char *p = start;
    while (*p) {
        if (*p == '{') depth++;
        else if (*p == '}') { depth--; if (depth == 0) return p; }
        p++;
    }
    return NULL;
}

static int find_and_parse_preset(const char *raw, const char *target_id, SensorProfile *prof) {
    const char *cursor = strstr(raw, "\"presets\"");
    if (!cursor) return 0;
    while ((cursor = strchr(cursor, '{')) != NULL) {
        const char *obj_end = find_object_end(cursor);
        if (!obj_end) { cursor++; continue; }

        const char *id_pos = json_find_key_bounded(cursor, obj_end, "id");
        if (!id_pos) { cursor = obj_end + 1; continue; }
        char this_id[64] = "";
        json_parse_string(id_pos, this_id, sizeof(this_id));
        if (strcmp(this_id, target_id) == 0) {
            const char *p;
            p = json_find_key_bounded(cursor, obj_end, "name");
            if (p) json_parse_string(p, prof->name, sizeof(prof->name));
            p = json_find_key_bounded(cursor, obj_end, "mode");
            if (p) json_parse_string(p, prof->mode, sizeof(prof->mode));
            p = json_find_key_bounded(cursor, obj_end, "duty");
            if (p) prof->duty = json_parse_int(p);
            p = json_find_key_bounded(cursor, obj_end, "sleep");
            if (!p) p = json_find_key_bounded(cursor, obj_end, "sleep_sec");
            if (p) prof->sleep_sec = json_parse_int(p);
            p = json_find_key_bounded(cursor, obj_end, "temps");
            if (p) json_parse_int_array(p, prof->temps, NUM_STEPS);
            p = json_find_key_bounded(cursor, obj_end, "ticks");
            if (p) json_parse_int_array(p, prof->ticks, NUM_STEPS);
            prof->loaded = 1;
            return 1;
        }
        cursor = obj_end + 1;
    }
    return 0;
}


// fallback profile
static void default_profile(SensorProfile *prof, int sensor_idx) {
    snprintf(prof->name, sizeof(prof->name), "Default");
    strcpy(prof->mode, "parallel");
    prof->duty = 0; prof->sleep_sec = 0;
    uint16_t dt[NUM_STEPS] = {150,200,250,300,350,400,350,300,250,200};
    uint16_t tk[NUM_STEPS] = { 22, 11, 11, 11, 11, 22, 11, 11, 11, 11};
    memcpy(prof->temps, dt, sizeof(dt));
    memcpy(prof->ticks, tk, sizeof(tk));
    prof->loaded = 0;
}

static uint8_t profile_op_mode(const SensorProfile *prof) {
    return strcmp(prof->mode, "sequential") == 0
        ? BME68X_SEQUENTIAL_MODE
        : BME68X_PARALLEL_MODE;
}

static void normalize_profile(SensorProfile *prof, int sensor_idx) {
    if (strcmp(prof->mode, "parallel") != 0 && strcmp(prof->mode, "sequential") != 0) {
        printf("[CONFIG] Sensor %d: Unknown mode '%s'; using parallel.\n",
               sensor_idx + 1, prof->mode);
        strcpy(prof->mode, "parallel");
    }

    for (int i = 0; i < NUM_STEPS; i++) {
        if (prof->ticks[i] == 0) prof->ticks[i] = 1;
        if (strcmp(prof->mode, "sequential") == 0 &&
            prof->ticks[i] > MAX_SEQUENTIAL_TICKS) {
            printf("[CONFIG] Sensor %d step %d: Sequential duration capped at %d ticks.\n",
                   sensor_idx + 1, i + 1, MAX_SEQUENTIAL_TICKS);
            prof->ticks[i] = MAX_SEQUENTIAL_TICKS;
        }
        if (prof->temps[i] > 400) prof->temps[i] = 400;
    }
}

// load one profile per sensor
static void load_all_profiles(SensorProfile profiles[NUM_SENSORS]) {
    for (int i = 0; i < NUM_SENSORS; i++) default_profile(&profiles[i], i);
    const char *config_path = getenv("BME_CONFIG_PATH");
    if (!config_path || config_path[0] == '\0') config_path = CONFIG_PATH;

    FILE *f = fopen(config_path, "r");
    if (!f && strcmp(config_path, LEGACY_CONFIG_PATH) != 0) {
        f = fopen(LEGACY_CONFIG_PATH, "r");
        if (f) config_path = LEGACY_CONFIG_PATH;
    }
    if (!f) {
        printf("[CONFIG] Warning: Could not open %s. Using defaults.\n", config_path);
        return;
    }
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f); rewind(f);
    char *raw = malloc(fsize + 1);
    if (!raw) { fclose(f); return; }
    fread(raw, 1, fsize, f);
    raw[fsize] = '\0';
    fclose(f);
    
    printf("[CONFIG] Successfully read %s (%ld bytes)\n", config_path, fsize);
    
    const char *apos = strstr(raw, "\"assignments\"");
    if (!apos) {
        printf("[CONFIG] Error: Could not find 'assignments' in JSON.\n");
        free(raw); return;
    }
    
    for (int i = 0; i < NUM_SENSORS; i++) {
        char key[4]; snprintf(key, sizeof(key), "%d", i + 1);
        const char *vp = json_find_key(apos, key);
        if (!vp) continue;
        char preset_id[64] = "";
        json_parse_string(vp, preset_id, sizeof(preset_id));
        if (preset_id[0] != '\0') {
            if (find_and_parse_preset(raw, preset_id, &profiles[i])) {
                printf("[CONFIG] Sensor %d loaded preset: '%s'\n", i+1, profiles[i].name);
            } else {
                printf("[CONFIG] Sensor %d: Preset '%s' not found! Using default.\n", i+1, preset_id);
            }
        }
    }
    for (int i = 0; i < NUM_SENSORS; i++) normalize_profile(&profiles[i], i);
    free(raw);
}

// create one session file per sensor
static FILE *open_session_file(int sensor_idx, const char *preset_name) {
    mkdir(MEASUREMENTS_DIR, 0777);
    time_t now = time(NULL);
    struct tm *t = localtime(&now);

    char safe_preset[64];
    strncpy(safe_preset, preset_name, 63);
    safe_preset[63] = '\0';
    for (int k = 0; safe_preset[k]; k++) {
        if (safe_preset[k] == ' ' || safe_preset[k] == '(' || safe_preset[k] == ')' || safe_preset[k] == '/') {
            safe_preset[k] = '_';
        }
    }

    char filepath[256];
    snprintf(filepath, sizeof(filepath),
             "%s/%04d_%02d_%02d_%02d%02d%02d_sensor%d_%s.csv",
             MEASUREMENTS_DIR,
             t->tm_year + 1900, t->tm_mon + 1, t->tm_mday,
             t->tm_hour, t->tm_min, t->tm_sec,
             sensor_idx + 1, safe_preset);

    FILE *f = fopen(filepath, "w");
    if (!f) {
        printf("[ERROR] Sensor %d: Failed to create file: %s\n", sensor_idx + 1, filepath);
        perror("[ERROR] Reason");
        return NULL;
    }
    printf("[FILE] Sensor %d: %s\n", sensor_idx + 1, filepath);
    fprintf(f, "Timestamp_ms,Sensor,Gas_Index,Target_Temp_C,"
               "Gas_Resistance_Ohms,Temperature_C,Humidity_perc,Pressure_hPa\n");
    fflush(f);
    return f;
}

static void write_live(LiveReading readings[NUM_SENSORS]) {
    FILE *f = fopen(LIVE_PATH, "w");
    if (!f) return;
    fprintf(f, "{\n  \"sensors\": [\n");
    for (int i = 0; i < NUM_SENSORS; i++) {
        if (readings[i].valid) {
            fprintf(f,
                "    { \"sensor\": %d, \"temperature\": %.2f, \"humidity\": %.2f, "
                "\"pressure\": %.2f, \"gasResistance\": %.2f, "
                "\"gasIndex\": %d, \"preset\": \"%s\" }%s\n",
                i+1, readings[i].temperature, readings[i].humidity,
                readings[i].pressure / 100.0f, readings[i].gas_resistance,
                readings[i].gas_index, readings[i].preset_name,
                (i < NUM_SENSORS-1) ? "," : "");
        } else {
            fprintf(f, "    { \"sensor\": %d, \"valid\": false }%s\n",
                    i+1, (i < NUM_SENSORS-1) ? "," : "");
        }
    }
    fprintf(f, "  ]\n}\n");
    fclose(f);
}

typedef struct {
    int sensor_idx;
    struct bme68x_dev *sensor;
    SensorProfile *profile;
    uint8_t op_mode;
} ThreadArgs;

static void write_csv_reading(int sensor_idx,
                              uint8_t gas_index,
                              const SensorProfile *profile,
                              const struct bme68x_data *data,
                              long long timestamp_ms) {
    if (!g_cycle_files[sensor_idx]) return;

    fprintf(g_cycle_files[sensor_idx],
            "%lld,%d,%d,%d,%.2f,%.2f,%.2f,%.2f\n",
            timestamp_ms, sensor_idx + 1,
            gas_index, profile->temps[gas_index],
            data->gas_resistance, data->temperature,
            data->humidity, data->pressure / 100.0f);
    fflush(g_cycle_files[sensor_idx]);
}

static void complete_cycle(int sensor_idx,
                           int *cycle_count,
                           const SensorProfile *profile,
                           struct bme68x_dev *sensor,
                           uint8_t op_mode) {
    (*cycle_count)++;

    pthread_mutex_lock(&log_mutex);
    printf("[OK] Sensor %d cycle %d complete. ", sensor_idx + 1, *cycle_count);

    if (profile->duty > 0 &&
        *cycle_count % profile->duty == 0 &&
        profile->sleep_sec > 0) {
        printf("Sleeping %ds (duty cycle)...\n", profile->sleep_sec);
        fflush(stdout);
        pthread_mutex_unlock(&log_mutex);
        bme68x_set_op_mode(BME68X_SLEEP_MODE, sensor);
        sleep(profile->sleep_sec);
    } else {
        printf("Continuing.\n");
        fflush(stdout);
        pthread_mutex_unlock(&log_mutex);
    }

    bme68x_set_op_mode(op_mode, sensor);
}

// sensor thread
void *sensor_thread_func(void *arg) {
    ThreadArgs *targ = (ThreadArgs *)arg;
    int s = targ->sensor_idx;
    struct bme68x_dev *sensor = targ->sensor;
    SensorProfile *profile = targ->profile;
    uint8_t op_mode = targ->op_mode;

    uint8_t            prev_idx = 255;
    uint16_t           step_count = 0;
    int                cycle_count = 0;
    struct bme68x_data stable;
    long long          stable_ts = 0;
    uint8_t            last_meas_index = 0;
    int                have_last_meas_index = 0;
    memset(&stable, 0, sizeof(stable));

    while (g_running) {
        usleep(PROFILE_TICK_MS * 1000);

        struct bme68x_data data[3];
        uint8_t n_fields = 0;

        if (bme68x_get_data(op_mode, data, &n_fields, sensor) != BME68X_OK
            || n_fields == 0) continue;

        for (uint8_t fi = 0; fi < n_fields; fi++) {
            if (data[fi].status != BME68X_VALID_DATA) continue;

            if (have_last_meas_index) {
                uint8_t delta = (uint8_t)(data[fi].meas_index - last_meas_index);
                if (delta == 0 || delta > 127) continue;
            }
            last_meas_index = data[fi].meas_index;
            have_last_meas_index = 1;

            uint8_t cur_idx = data[fi].gas_index;
            if (cur_idx >= NUM_STEPS) continue;

            struct timeval tv;
            gettimeofday(&tv, NULL);
            long long ts_ms = (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;

            // update live data
            pthread_mutex_lock(&live_mutex);
            live[s].temperature    = data[fi].temperature;
            live[s].humidity       = data[fi].humidity;
            live[s].pressure       = data[fi].pressure;
            live[s].gas_resistance = data[fi].gas_resistance;
            live[s].gas_index      = cur_idx;
            live[s].valid          = 1;
            pthread_mutex_unlock(&live_mutex);

            // sequential mode returns one reading per heater step
            if (op_mode == BME68X_SEQUENTIAL_MODE) {
                write_csv_reading(s, cur_idx, profile, &data[fi], ts_ms);
                if (cur_idx == (NUM_STEPS - 1)) {
                    complete_cycle(s, &cycle_count, profile, sensor, op_mode);
                    prev_idx = 255;
                    break;
                }
                continue;
            }

            // save the last stable parallel reading
            if (cur_idx != prev_idx) {
                if (prev_idx != 255)
                    write_csv_reading(s, prev_idx, profile, &stable, stable_ts);
                step_count = 0;
            }

            stable    = data[fi];
            stable_ts = ts_ms;
            prev_idx  = cur_idx;
            step_count++;

            // save the last heater step
            if (cur_idx == (NUM_STEPS-1) &&
                step_count == profile->ticks[NUM_STEPS-1]) {

                write_csv_reading(s, cur_idx, profile, &stable, stable_ts);
                complete_cycle(s, &cycle_count, profile, sensor, op_mode);
                prev_idx = 255;
            }
        }
    }
    return NULL;
}

int main(int argc, char *argv[]) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    printf("\nBME688 MEASUREMENTS\n");
    signal(SIGTERM, handle_shutdown);
    signal(SIGINT,  handle_shutdown);

    for (int i = 0; i < NUM_SENSORS; i++) g_cycle_files[i] = NULL;

    SensorProfile profiles[NUM_SENSORS];
    load_all_profiles(profiles);

    if (linux_hal_init() != 0) return -1;

    struct bme68x_dev sensors[NUM_SENSORS];
    uint8_t sensor_ids[NUM_SENSORS];
    uint8_t op_modes[NUM_SENSORS];
    uint16_t sequential_durations[NUM_SENSORS][NUM_STEPS];

    for (int i = 0; i < NUM_SENSORS; i++) {
        sensor_ids[i] = (uint8_t)i;
        sensors[i].intf     = BME68X_SPI_INTF;
        sensors[i].read     = locked_spi_read;
        sensors[i].write    = locked_spi_write;
        sensors[i].delay_us = my_delay_us;
        sensors[i].intf_ptr = &sensor_ids[i];
        sensors[i].amb_temp = 25;

        if (bme68x_init(&sensors[i]) != BME68X_OK) return -1;
    }

    struct bme68x_conf conf;
    conf.filter  = BME68X_FILTER_OFF;
    conf.odr     = BME68X_ODR_NONE;
    conf.os_hum  = BME68X_OS_1X;
    conf.os_pres = BME68X_OS_16X;
    conf.os_temp = BME68X_OS_2X;

    for (int i = 0; i < NUM_SENSORS; i++) {
        op_modes[i] = profile_op_mode(&profiles[i]);
        if (bme68x_set_conf(&conf, &sensors[i]) != BME68X_OK) return -1;

        struct bme68x_heatr_conf hc;
        memset(&hc, 0, sizeof(hc));
        hc.enable          = BME68X_ENABLE;
        hc.heatr_temp_prof = profiles[i].temps;
        hc.profile_len     = NUM_STEPS;

        if (op_modes[i] == BME68X_SEQUENTIAL_MODE) {
            for (int step = 0; step < NUM_STEPS; step++) {
                sequential_durations[i][step] = profiles[i].ticks[step] * PROFILE_TICK_MS;
            }
            hc.heatr_dur_prof = sequential_durations[i];
        } else {
            hc.heatr_dur_prof = profiles[i].ticks;
            uint32_t md = bme68x_get_meas_dur(BME68X_PARALLEL_MODE, &conf, &sensors[i]) / 1000;
            hc.shared_heatr_dur = (md >= PROFILE_TICK_MS)
                ? 5
                : (uint16_t)(PROFILE_TICK_MS - md);
        }

        if (bme68x_set_heatr_conf(op_modes[i], &hc, &sensors[i]) != BME68X_OK) return -1;
        if (bme68x_set_op_mode(op_modes[i], &sensors[i]) != BME68X_OK) return -1;
        printf("[CONFIG] Sensor %d operating in %s mode.\n", i + 1, profiles[i].mode);
    }

    for (int i = 0; i < NUM_SENSORS; i++) {
        g_cycle_files[i] = open_session_file(i, profiles[i].name);
        memset(&live[i], 0, sizeof(live[i]));
        strncpy(live[i].preset_name, profiles[i].name, 63);
    }

    pthread_t threads[NUM_SENSORS];
    ThreadArgs targs[NUM_SENSORS];

    printf("\n[START] Starting 8 independent sensor threads (Protected by Mutexes).\n");
    for (int i = 0; i < NUM_SENSORS; i++) {
        targs[i].sensor_idx = i;
        targs[i].sensor = &sensors[i];
        targs[i].profile = &profiles[i];
        targs[i].op_mode = op_modes[i];
        pthread_create(&threads[i], NULL, sensor_thread_func, &targs[i]);
    }

    // update live data twice per second
    while (g_running) {
        usleep(500000); 
        pthread_mutex_lock(&live_mutex);
        write_live(live);
        pthread_mutex_unlock(&live_mutex);
    }

    for (int i = 0; i < NUM_SENSORS; i++) {
        pthread_join(threads[i], NULL);
    }

    return 0;
}
