using System;
namespace dotnet
{
    class Program
    {
        public static int Fibonacci(int n)
        {
            if (n==0 || n==1) {
                return  n;
            }
            return ( Fibonacci( n - 1 ) + Fibonacci( n - 2 ) );
        }
        
        static void Main()
        {
            for (int i = 0; i < 200; i++)
            {
                Fibonacci(i);
            }
        }
    }
}