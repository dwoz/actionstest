import argparse
import asyncio
import base64
import concurrent
import io
import logging
import time
import sys
import json
import sys

import aiortc.exceptions
from aiortc import RTCIceCandidate, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.signaling import BYE, add_signaling_arguments, create_signaling, object_to_string, object_from_string

log = logging.getLogger(__name__)



def print_pastable(data, message="offer"):
    sys.stdout.write(f"-- {message}--" + "\n\n")
    sys.stdout.write(f"{data.decode()}" + "\n\n")
    sys.stdout.write(f"-- end {message} --" + "\n\n")
    sys.stdout.flush()


class ProxyClient:

    def __init__(self, args, channel):
        self.args = args
        self.channel = channel

    def start(self):
        self.channel.on("message")(self.on_message)

    def on_message(self, message):
        msg = json.loads(message)
        key = msg["key"]
        data = msg["data"]
        log.debug("new connection messsage %s", key)

        pc = RTCPeerConnection()
        @pc.on("datachannel")
        def on_channel(channel):
            log.info("Sub channel established %s", key)
            asyncio.ensure_future(self.handle_channel(channel))

        async def finalize_connection():
            obj = object_from_string(data)
            if isinstance(obj, RTCSessionDescription):
                await pc.setRemoteDescription(obj)
                if obj.type == "offer":
                    # send answer
                    await pc.setLocalDescription(await pc.createAnswer())
                    msg = {
                       "key": key,
                       "data": object_to_string(pc.localDescription)
                    }
                    self.channel.send(json.dumps(msg))
            elif isinstance(obj, RTCIceCandidate):
                await pc.addIceCandidate(obj)
            elif obj is BYE:
                log.warning("Exiting")

        asyncio.ensure_future(finalize_connection())


    async def handle_channel(self, channel):
        try:
            reader, writer = await asyncio.open_connection(
                '127.0.0.1', self.args.port)
            log.info("opened connection to port %s", self.args.port)

            @channel.on("message")
            def on_message(message):
                log.debug("rtc to socket %r", message)
                writer.write(message)
                asyncio.ensure_future(writer.drain())

            while True:
                data = await reader.read(100)
                if data:
                    log.debug("socket to rtc %r", data)
                    channel.send(data)
        except Exception:
            log.exception("WTF4")

class ProxyServer:

    def __init__(self, args, channel):
        self.args = args
        self.channel = channel
        self.connections = {}

    async def start(self):
        @self.channel.on("message")
        def handle_message(message):
            asyncio.ensure_future(self.handle_message(message))

        self.server = await asyncio.start_server(
            self.new_connection, '127.0.0.1', self.args.port)
        log.info("Listening on port %s",  self.args.port)
        async with self.server:
            await self.server.serve_forever()

    async def handle_message(self, message):
        msg = json.loads(message)
        key = msg["key"]
        pc = self.connections[key].pc
        channel = self.connections[key].channel
        obj = object_from_string(msg["data"])
        if isinstance(obj, RTCSessionDescription):
            await pc.setRemoteDescription(obj)
            if obj.type == "offer":
                # send answer
                await pc.setLocalDescription(await pc.createAnswer())
                msg = {"key": key,
                       "data": object_to_string(pc.localDescription),
                      }
                self.channel.send(json.dumps(msg))
        elif isinstance(obj, RTCIceCandidate):
            await pc.addIceCandidate(obj)
        elif obj is BYE:
            print("Exiting")

    async def new_connection(self, reader, writer):
        try:
            info = writer.get_extra_info("peername")
            key = f"{info[0]}:{info[1]}"
            log.info("Connection from %s", key)
            pc = RTCPeerConnection()
            channel = pc.createDataChannel("{key}")

            async def readerproxy():
                while True:
                    data = await reader.read(100)
                    if data:
                        log.debug("socket to rtc %r", data)
                        try:
                            channel.send(data)
                        except aiortc.exceptions.InvalidStateError:
                            log.error("Channel was in an invalid state %s, bailing reader coroutine", key)
                            break


            @channel.on("open")
            def on_open():
                asyncio.ensure_future(readerproxy())


            @channel.on("message")
            def on_message(message):
                log.debug("rtc to socket %r", message)
                writer.write(message)
                asyncio.ensure_future(writer.drain())

            self.connections[key] = ProxyConnection(pc, channel)
            await pc.setLocalDescription(await pc.createOffer())
            msg = {
                "key": key,
                "data": object_to_string(pc.localDescription),
            }
            log.debug("Send new offer")
            self.channel.send(json.dumps(msg, sort_keys=True))
        except Exception:
            log.exception("WTF")


class ProxyConnection:
    def __init__(self, pc, channel):
        self.pc = pc
        self.channel = channel


async def read_from_stdin():
    loop = asyncio.get_event_loop()
    if sys.platform == "win32":
        print("-- Please enter a message from remote party --")
        input_stream = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
        _ = ""
        while True:
            _ += input_stream.readline()
            try:
                json.loads(_)
            except:
                pass
            else:
                return _
        #_ = ""
        #while True:
        #    _ +=  input("-- Please enter a message from remote party --")
        #    try:
        #        json.loads(_)
        #    except:
        #        pass
        #    else:
        #        return _
    else:
        print("-- Please enter a message from remote party --")
        reader = asyncio.StreamReader(loop=loop)
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        return (await reader.readline()).decode(sys.stdin.encoding)

async def run_answer(pc, args):
    """
    Top level offer answer server.
    """

    @pc.on("datachannel")
    def on_datachannel(channel):
        log.info("Channel created")
        client = ProxyClient(args, channel)
        client.start()

    data = await read_from_stdin()
    log.error("Data from stdin %r", data)
    obj = object_from_string(base64.b64decode(data))
    if isinstance(obj, RTCSessionDescription):
        log.debug("received rtc session description")
        await pc.setRemoteDescription(obj)
        if obj.type == "offer":
            # send answer
            await pc.setLocalDescription(await pc.createAnswer())
            data = base64.b64encode(object_to_string(pc.localDescription).encode('utf-8'))
            print_pastable(data, "reply")
    elif isinstance(obj, RTCIceCandidate):
        log.debug("received rtc ice candidate")
        await pc.addIceCandidate(obj)
    elif obj is BYE:
        print("Exiting")

    while True:
        await asyncio.sleep(.3)



async def run_offer(pc, args):
    """
    Top level offer server this will estabilsh a data channel and start a tcp
    server on the port provided. New connections to the server will start the
    creation of a new rtc connectin and a new data channel used for proxying
    the client's connection to the remote side.
    """
    control_channel = pc.createDataChannel("main")
    log.info("Created control channel.")

    async def start_server():
        """
        Start the proxy server. The proxy server will create a local port and
        handle creation of additional rtc peer connections for each new client
        to the proxy server port.
        """
        server = ProxyServer(args, control_channel)
        await server.start()

    @control_channel.on("open")
    def on_open():
        """
        Start the proxy server when the control channel is connected.
        """
        asyncio.ensure_future(start_server())

    # send offer
    await pc.setLocalDescription(await pc.createOffer())
    print_pastable(base64.b64encode(object_to_string(pc.localDescription).encode('utf-8')), "offer")
    print("-- Please enter a message from remote party --")
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader(loop=loop)
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )

    data = await reader.readline()
    obj = object_from_string(base64.b64decode(data.decode(sys.stdin.encoding)))
    if isinstance(obj, RTCSessionDescription):
        log.debug("received rtc session description")
        await pc.setRemoteDescription(obj)
        if obj.type == "offer":
            # send answer
            await pc.setLocalDescription(await pc.createAnswer())
            await signaling.send(pc.localDescription)
    elif isinstance(obj, RTCIceCandidate):
        log.debug("received rtc ice candidate")
        await pc.addIceCandidate(obj)
    elif obj is BYE:
        print("Exiting")

    while True:
        await asyncio.sleep(.3)



if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    parser = argparse.ArgumentParser(description="Port proxy")
    parser.add_argument("role", choices=["offer", "answer"])
    parser.add_argument("--port", type=int, default=11224)
    parser.add_argument("--verbose", "-v", action="count", default=None)
    args = parser.parse_args()


    if args.verbose is None:
        logging.basicConfig(level=logging.WARNING)
    elif args.verbose > 1:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    pc = RTCPeerConnection()
    if args.role == "offer":
        coro = run_offer(pc, args)
    else:
        coro = run_answer(pc, args)

    # run event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(pc.close())
