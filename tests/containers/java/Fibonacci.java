//
// Copyright (c) Granulate. All rights reserved.
// Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
//
import java.io.File;

public class Fibonacci {
    private static long fibonacci(final int n) {
        return n <= 1 ? n : fibonacci(n - 1) + fibonacci(n - 2);
    }

    public static void main(final String[] args) {
        System.out.println("Fibonacci thread starting");
        Thread thread = new Thread() {
            public void run() {
                while (true) {
                    try {
                        new File("/").list();
                    } catch (Exception e) {
                    }
                }
            }
        };
        thread.start();
        System.err.println("Fibonacci loop starting");
        while (true) {
            fibonacci(30);
        }
    }
}
