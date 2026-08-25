package streamstats.scala

object NativeTest:
  def main(args:Array[String]):Unit=
    val w=WindowStatistics(3);Array(1.0,2.0,3.0,4.0).foreach(w.add)
    val s=w.snapshot();assert(s.count==3&&s.sum==9&&math.abs(s.variance-2.0/3)<1e-12);assert(w.percentile(50)==3);w.validate()
