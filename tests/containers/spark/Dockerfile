# This docker image is used by `tests/test_spark.py`.
# It uses the official Spark docker image and runs the SparkPi example, with master only (no workers).

FROM apache/spark:3.4.0

# Runs SparkPi with 1000 as argument
CMD ["/bin/bash", "-c", "/opt/spark/sbin/start-master.sh && sleep 5 && /opt/spark/bin/spark-submit --class org.apache.spark.examples.SparkPi --master spark://$SPARK_MASTER_HOST:7077 /opt/spark/examples/jars/spark-examples_2.12-3.4.0.jar 1000"]
