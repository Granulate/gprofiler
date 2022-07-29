#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

def fibonacci( n )
    puts "SIUR"
    puts n
    return  n  if ( 0..1 ).include? n
    ( fibonacci( n - 1 ) + fibonacci( n - 2 ) )
end

for $i in 0..3
    puts fibonacci( $i )
    STDOUT.flush
end
