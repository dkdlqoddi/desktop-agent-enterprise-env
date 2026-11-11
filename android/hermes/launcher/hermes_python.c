// The Python executable of the Android app's Hermes runtime.
//
// The python.org Android packages have no executable, only libpython. Android lets an app execute files from its
// native library directory and nowhere else, so build_runtime.py compiles this file into lib/<abi>/libhermes_python.so
// and the app runs it as a child process whose stdin and stdout carry the JSON lines of the worker protocol. Killing
// that process stops a turn at once.
//
// It behaves like the python command: the arguments (-I -B script.py ...) are parsed by CPython as usual.

#include <Python.h>

#include <dirent.h>
#include <dlfcn.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Loads the other libraries of CPython's Android package (lib*_python.so: OpenSSL, SQLite) from this executable's
// directory. Extension modules link them by name, and a library that is loaded already is found by that name even
// where LD_LIBRARY_PATH does not reach the dynamic linker (an ARM runtime under binary translation). Python loads them
// anyway as soon as Hermes imports ssl, hashlib or sqlite3.
static void preload_libraries(const char *executable) {
    char directory[PATH_MAX];
    const char *slash = strrchr(executable, '/');
    if (slash == NULL || (size_t)(slash - executable) >= sizeof(directory)) {
        return;
    }
    memcpy(directory, executable, (size_t)(slash - executable));
    directory[slash - executable] = '\0';

    // A library whose dependency is not loaded yet fails; the next round loads it after that dependency.
    for (int round = 0; round < 4; round++) {
        int failed = 0;
        DIR *folder = opendir(directory);
        if (folder == NULL) {
            return;
        }
        struct dirent *entry;
        while ((entry = readdir(folder)) != NULL) {
            const char *name = entry->d_name;
            size_t length = strlen(name);
            if (strncmp(name, "lib", 3) != 0 || length < 14 || strcmp(name + length - 10, "_python.so") != 0 ||
                strcmp(name, slash + 1) == 0) {
                continue;
            }
            char path[PATH_MAX];
            if (snprintf(path, sizeof(path), "%s/%s", directory, name) >= (int)sizeof(path)) {
                continue;
            }
            if (dlopen(path, RTLD_NOW | RTLD_LOCAL) == NULL) {
                failed++;
            }
        }
        closedir(folder);
        if (failed == 0) {
            return;
        }
    }
}

int main(int argc, char **argv) {
    // An app process blocks some signals for its runtime's own threads, and a child inherits that mask through exec.
    // Start from an empty mask, as a shell would.
    sigset_t signals;
    sigemptyset(&signals);
    sigprocmask(SIG_SETMASK, &signals, NULL);

    // -I makes CPython ignore PYTHONHOME like every PYTHON* variable, and this file lives in the native library
    // directory, far from the extracted standard library. So the launcher applies the variable itself.
    const char *home = getenv("PYTHONHOME");
    if (home == NULL || home[0] == '\0') {
        fputs("hermes-python: PYTHONHOME is not set\n", stderr);
        return 2;
    }
    if (argc > 0) {
        preload_libraries(argv[0]);
    }

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    status = PyConfig_SetBytesArgv(&config, argc, argv);
    if (PyStatus_Exception(status)) {
        goto fail;
    }
    status = PyConfig_SetBytesString(&config, &config.home, home);
    if (PyStatus_Exception(status)) {
        goto fail;
    }
    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) {
        goto fail;
    }
    PyConfig_Clear(&config);
    return Py_RunMain();

fail:
    PyConfig_Clear(&config);
    if (PyStatus_IsExit(status)) {
        return status.exitcode;
    }
    Py_ExitStatusException(status);
}
