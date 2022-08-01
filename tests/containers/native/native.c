// This one isn't fibonacci like the others, because it's used in tests for perf smart mode,
// which require consistent stacktraces (and fibonacci is not consistent; these stacks are)
static void recursive(unsigned int n) {
    if (n > 0) {
        recursive(n - 1);
    }

    while (1) ;
}

int main(void) {
    recursive(10);
    return 0;
}
