#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/prctl.h>
#include <signal.h>

/*
  preexec_fn is not safe to use in the presence of threads,
  child process could deadlock before exec is called.
  this little shim is a workaround to avoid using preexe_fn and
  still get the desired behavior (PR_SET_PDEATHSIG).
*/
int main(int argc, char *argv[]) {
  if (argc < 2) {
    fprintf(stderr, "Usage: %s /path/to/binary [args...]\n", argv[0]);
    return 1;
  }

  if (prctl(PR_SET_PDEATHSIG, SIGKILL) == -1) {
    perror("prctl");
    return 1;
  }

  execvp(argv[1], &argv[1]);

  perror("execvp");
  return 1;
}
