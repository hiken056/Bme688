#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#define NUM_SENSORS 8
#define NUM_STEPS 10

typedef struct {
    char     name[64];
    char     mode[16];
    int      duty;
    int      sleep_sec;
    uint16_t temps[NUM_STEPS];
    uint16_t ticks[NUM_STEPS];
    int      loaded;
} SensorProfile;

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

static int find_and_parse_preset(const char *raw, const char *target_id, SensorProfile *prof) {
    const char *cursor = strstr(raw, "\"presets\"");
    if (!cursor) return 0;
    while ((cursor = strchr(cursor, '{')) != NULL) {
        const char *id_pos = json_find_key(cursor, "id");
        if (!id_pos) { cursor++; continue; }
        char this_id[64] = "";
        json_parse_string(id_pos, this_id, sizeof(this_id));
        if (strcmp(this_id, target_id) == 0) {
            const char *p;
            p = json_find_key(cursor, "name");
            if (p) json_parse_string(p, prof->name, sizeof(prof->name));
            p = json_find_key(cursor, "mode");
            if (p) json_parse_string(p, prof->mode, sizeof(prof->mode));
            p = json_find_key(cursor, "duty");
            if (p) prof->duty = json_parse_int(p);
            p = json_find_key(cursor, "sleep");
            if (p) prof->sleep_sec = json_parse_int(p);
            p = json_find_key(cursor, "temps");
            if (p) json_parse_int_array(p, prof->temps, NUM_STEPS);
            p = json_find_key(cursor, "ticks");
            if (p) json_parse_int_array(p, prof->ticks, NUM_STEPS);
            prof->loaded = 1;
            return 1;
        }
        cursor++;
    }
    return 0;
}

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

static void load_all_profiles(SensorProfile profiles[NUM_SENSORS]) {
    for (int i = 0; i < NUM_SENSORS; i++) default_profile(&profiles[i], i);
    const char *config_path = getenv("BME_CONFIG_PATH");
    if (!config_path || config_path[0] == '\0') {
        config_path = "/var/lib/bme688/config.json";
    }
    FILE *f = fopen(config_path, "r");
    if (!f) f = fopen("/tmp/bme_config.json", "r");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long fsize = ftell(f); rewind(f);
    char *raw = malloc(fsize + 1);
    if (!raw) { fclose(f); return; }
    fread(raw, 1, fsize, f);
    raw[fsize] = '\0';
    fclose(f);
    const char *apos = strstr(raw, "\"assignments\"");
    if (!apos) { free(raw); return; }
    for (int i = 0; i < NUM_SENSORS; i++) {
        char key[4]; snprintf(key, sizeof(key), "%d", i + 1);
        const char *vp = json_find_key(apos, key);
        if (!vp) continue;
        char preset_id[64] = "";
        json_parse_string(vp, preset_id, sizeof(preset_id));
        if (preset_id[0] != '\0') find_and_parse_preset(raw, preset_id, &profiles[i]);
    }
    free(raw);
}

int main() {
    SensorProfile profiles[NUM_SENSORS];
    load_all_profiles(profiles);
    for(int i=0; i<NUM_SENSORS; i++) {
        printf("S%d -> %s\n", i+1, profiles[i].name);
    }
    return 0;
}
