# mcr.microsoft.com/dotnet/sdk:6.0-focal
FROM  mcr.microsoft.com/dotnet/sdk@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22

WORKDIR /app/Fibonacci

ADD Fibonacci.cs /app/Fibonacci
RUN dotnet new console
RUN rm Program.cs
RUN dotnet publish -c Release -o .

CMD ["dotnet", "Fibonacci.dll"]