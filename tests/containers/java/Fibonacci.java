//
// Copyright (C) 2023 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
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
