package streamstats.scala

import java.io.*
import java.nio.{ByteBuffer,ByteOrder}
import java.util.HashMap

object Worker:
  private val Protocol=1;private val MaxFrame=64*1024*1024
  private val Hello=0;private val Create=1;private val Add=2;private val AddMany=3;private val RemoveOldest=4;private val Remove=5;private val Clear=6;private val SnapshotCommand=7;private val Percentile=8;private val PercentileOf=9;private val Validate=10;private val Close=11;private val Shutdown=12
  private val windows=HashMap[java.lang.Long,WindowStatistics]();private var nextHandle=1L
  private def little(data:Array[Byte])=ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN)
  private def readFrame(in:InputStream):Array[Byte]|Null=
    val header=in.readNBytes(4);if header.length==0 then return null;if header.length!=4 then throw EOFException()
    val size=little(header).getInt;if size<5||size>MaxFrame then throw IOException("invalid frame")
    val data=in.readNBytes(size);if data.length!=size then throw EOFException();data
  private def writeFrame(out:OutputStream,data:Array[Byte]):Unit=
    val h=ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(data.length);out.write(h.array);out.write(data);out.flush()
  private def get(handle:Long):WindowStatistics=
    val result=windows.get(handle);if result==null then throw WindowException(6,"closed handle");result
  private def execute(request:Array[Byte]):Array[Byte]=
    val in=little(request);val id=in.getInt;val command=in.get.toInt&0xff
    val out=ByteBuffer.allocate(math.max(96,request.length*2+80)).order(ByteOrder.LITTLE_ENDIAN);out.putInt(id);out.putInt(0)
    try {
      command match {
        case Hello => out.putInt(Protocol); out.putInt(MaxFrame)
        case Create =>
          val handle=nextHandle;nextHandle+=1
          windows.put(handle,WindowStatistics(Math.toIntExact(in.getLong)))
          out.putLong(handle)
        case Add =>
          val w=get(in.getLong);val evicted=w.add(in.getDouble)
          out.put(if evicted then 1.toByte else 0.toByte)
          out.putDouble(if evicted then w.lastEvicted else 0)
        case AddMany =>
          val w=get(in.getLong);val n=in.getInt;out.putInt(n)
          val values=Array.ofDim[Double](n);var i=0
          while i<n do
            values(i)=in.getDouble
            if !java.lang.Double.isFinite(values(i)) then throw WindowException(1,"invalid value")
            i+=1
          i=0
          while i<n do
            val evicted=w.add(values(i))
            out.put(if evicted then 1.toByte else 0.toByte)
            out.putDouble(if evicted then w.lastEvicted else 0)
            i+=1
        case RemoveOldest => out.putDouble(get(in.getLong).removeOldest())
        case Remove => get(in.getLong).remove(in.getDouble)
        case Clear => get(in.getLong).clear()
        case SnapshotCommand =>
          val s=get(in.getLong).snapshot()
          out.putLong(s.count);out.putDouble(s.sum);out.put(if s.count>0 then 1.toByte else 0.toByte)
          if s.count>0 then
            out.putDouble(s.min);out.putDouble(s.max);out.putDouble(s.mean);out.putDouble(s.variance);out.putDouble(s.std)
        case Percentile => out.putDouble(get(in.getLong).percentile(in.getDouble))
        case PercentileOf => out.putDouble(get(in.getLong).percentileOf(in.getDouble))
        case Validate => get(in.getLong).validate()
        case Close => windows.remove(in.getLong)
        case Shutdown => windows.clear()
        case _ => throw WindowException(1,"unknown command")
      }
    } catch {
      case error:WindowException => out.putInt(4,error.status)
      case _:Throwable => out.putInt(4,5)
    }
    val result=Array.ofDim[Byte](out.position);out.flip;out.get(result);result
  def main(args:Array[String]):Unit=
    var running=true
    while running do
      val frame=readFrame(System.in)
      if frame==null then return
      val command=frame.nn(4).toInt&0xff
      writeFrame(System.out,execute(frame.nn))
      if command==Shutdown then running=false
