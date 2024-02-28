<?php
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

function Fibonacci($number){

    if ($number == 0)
        return 0;
    else if ($number == 1)
        return 1;

    else
        return (Fibonacci($number-1) +
                Fibonacci($number-2));
}

$number = 200;
for ($counter = 0; $counter < $number; $counter++){
    echo Fibonacci($counter) . PHP_EOL;
}
?>
