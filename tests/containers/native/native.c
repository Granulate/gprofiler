// This one isn't fibonacci like the others, because it's used in tests for perf smart mode,
// which require consistent stacktraces (and fibonacci is not consistent; these stacks are)
#define _GNU_SOURCE
#include <pthread.h>
#include <stdlib.h>

static void recursive(unsigned int n) {
    if (n > 0) {
        recursive(n - 1);
    }

    while (1) ;
}

#if defined(CHANGE_COMM) || defined(THREAD_COMM)
static void change_my_comm(void) {
    char name[16];  // 16 -> TASK_COMM_LEN
    if (pthread_getname_np(pthread_self(), name, sizeof(name))) {
        exit(1);
    }
    name[0]++; // get the name changed
    if (pthread_setname_np(pthread_self(), name)) {
        exit(1);
    }
}
#endif

#if defined(CHANGE_COMM) && defined(THREAD_COMM)
#error not both
#endif

#if defined(CHANGE_COMM)
int main(void) {
    change_my_comm();
    recursive(10);
    return 0;
}
#elif defined(THREAD_COMM)
static void *thread(void *_) {
    change_my_comm();
    recursive(10);
}

int main(void) {
    change_my_comm();  // change first time in main thread
    pthread_t t;
    pthread_create(&t, NULL, thread, NULL);
    pthread_join(t, NULL);
    return 0;
}
#else
int main(void) {
    recursive(10);
    return 0;
}
#endif
