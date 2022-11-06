//
// Copyright (c) Granulate. All rights reserved.
// Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
//
function fibonacci(n) {
    return n <= 1 ? n : fibonacci(n - 1) + fibonacci(n - 2);
}

const {execSync} = require('child_process');
while (true) {
    fibonacci(30);
    execSync('sleep 0.01');
}
