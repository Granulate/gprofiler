FROM apache/spark

# Runs SparkPi with 1000 as argument
CMD ["/bin/bash", "-c", "/opt/spark/sbin/start-master.sh && sleep 5 && /opt/spark/bin/spark-submit --class org.apache.spark.examples.SparkPi --master spark://$(hostname -f):7077 /opt/spark/examples/jars/spark-examples_2.12-3.4.0.jar 1000"]