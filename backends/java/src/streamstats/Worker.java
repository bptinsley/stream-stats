package streamstats;

import java.io.*;
import java.nio.*;
import java.util.HashMap;

public final class Worker {
    static final int PROTOCOL=1, MAX_FRAME=64*1024*1024;
    static final byte HELLO=0,CREATE=1,ADD=2,ADD_MANY=3,REMOVE_OLDEST=4,REMOVE=5,CLEAR=6,SNAPSHOT=7,PERCENTILE=8,PERCENTILE_OF=9,VALIDATE=10,CLOSE=11,SHUTDOWN=12;
    private final HashMap<Long,WindowStatistics> windows=new HashMap<>();private long nextHandle=1;
    private static ByteBuffer little(byte[] data){return ByteBuffer.wrap(data).order(ByteOrder.LITTLE_ENDIAN);}
    private static void writeFrame(OutputStream out,byte[] data)throws IOException{ByteBuffer h=ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(data.length);out.write(h.array());out.write(data);out.flush();}
    private static byte[] readFrame(InputStream in)throws IOException{byte[] h=in.readNBytes(4);if(h.length==0)return null;if(h.length!=4)throw new EOFException();int n=little(h).getInt();if(n<5||n>MAX_FRAME)throw new IOException("invalid frame length");byte[] d=in.readNBytes(n);if(d.length!=n)throw new EOFException();return d;}
    private WindowStatistics get(long h)throws WindowStatistics.WindowException{WindowStatistics w=windows.get(h);if(w==null)throw new WindowStatistics.WindowException(6,"closed handle");return w;}
    private byte[] execute(byte[] request){ByteBuffer in=little(request);int id=in.getInt();byte command=in.get();ByteArrayOutputStream bytes=new ByteArrayOutputStream();try(DataOutputStream ignored=new DataOutputStream(bytes)){
        ByteBuffer out=ByteBuffer.allocate(Math.max(32,request.length*2+80)).order(ByteOrder.LITTLE_ENDIAN);out.putInt(id);out.putInt(0);
        try{switch(command){case HELLO->out.putInt(PROTOCOL).putInt(MAX_FRAME);case CREATE->{long h=nextHandle++;windows.put(h,new WindowStatistics(Math.toIntExact(in.getLong())));out.putLong(h);}case ADD->{WindowStatistics w=get(in.getLong());boolean evicted=w.add(in.getDouble());out.put((byte)(evicted?1:0));out.putDouble(evicted?w.lastEvicted():0);}case ADD_MANY->{WindowStatistics w=get(in.getLong());int n=in.getInt();out.putInt(n);double[] values=new double[n];for(int i=0;i<n;i++){values[i]=in.getDouble();if(!Double.isFinite(values[i]))throw new WindowStatistics.WindowException(1,"value must be finite");}for(double v:values){boolean evicted=w.add(v);out.put((byte)(evicted?1:0));out.putDouble(evicted?w.lastEvicted():0);}}case REMOVE_OLDEST->out.putDouble(get(in.getLong()).removeOldest());case REMOVE->{get(in.getLong()).remove(in.getDouble());}case CLEAR->get(in.getLong()).clear();case SNAPSHOT->{WindowStatistics.Snapshot s=get(in.getLong()).snapshot();out.putLong(s.count()).putDouble(s.sum()).put((byte)(s.count()>0?1:0));if(s.count()>0)out.putDouble(s.min()).putDouble(s.max()).putDouble(s.mean()).putDouble(s.variance()).putDouble(s.std());}case PERCENTILE->out.putDouble(get(in.getLong()).percentile(in.getDouble()));case PERCENTILE_OF->out.putDouble(get(in.getLong()).percentileOf(in.getDouble()));case VALIDATE->get(in.getLong()).validate();case CLOSE->windows.remove(in.getLong());case SHUTDOWN->{windows.clear();}default->throw new WindowStatistics.WindowException(1,"unknown command");}}
        catch(WindowStatistics.WindowException|ArithmeticException e){int status=e instanceof WindowStatistics.WindowException we?we.status:1;out.putInt(4,status);}
        byte[] result=new byte[out.position()];out.flip();out.get(result);return result;
    }catch(IOException impossible){throw new AssertionError(impossible);}catch(Throwable unexpected){ByteBuffer out=ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN).putInt(id).putInt(5);return out.array();}}
    public void run()throws IOException{InputStream in=System.in;OutputStream out=System.out;while(true){byte[] frame=readFrame(in);if(frame==null)return;byte command=frame[4];writeFrame(out,execute(frame));if(command==SHUTDOWN)return;}}
    public static void main(String[] args)throws Exception{new Worker().run();}
}
