//
// Copyright (c) Granulate. All rights reserved.
// Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
//
public class Fibonacci {
    private static long fibonacci(final int n) {
        return n <= 1 ? n : fibonacci(n - 1) + fibonacci(n - 2);
    }

    public static void main(final String[] args) {
        while (true) {
            fibonacci(30);
        }
    }
}
