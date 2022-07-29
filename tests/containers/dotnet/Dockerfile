FROM  mcr.microsoft.com/dotnet/sdk@sha256:749439ff7a431ab4bc38d43cea453dff9ae1ed89a707c318b5082f9b2b25fa22

WORKDIR /app

RUN dotnet new console
RUN rm Program.cs
ADD Fibonacci.cs /app

CMD ["dotnet", "run", "Fibonacci.cs"]
