//
// Copyright (c) Granulate. All rights reserved.
// Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
//
package main

func fibonacci(n int) int {
	if n > 1 {
		return fibonacci(n - 1) + fibonacci(n - 2)
	}
	return n
}

func main() {
	for {
		fibonacci(30);
	}
}
