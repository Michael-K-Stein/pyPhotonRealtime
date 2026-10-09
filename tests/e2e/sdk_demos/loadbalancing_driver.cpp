// Replaces demo_loadBalancing's StdIO_main.cpp so tests can drive the demo.
//
// The stock main polls the keyboard with _kbhit(), which never fires on a
// piped stdin. This one reads the same menu keys ('1'-'4', '0' to exit) one
// per line from stdin and otherwise runs the demo's NetworkLogic unchanged.
// EOF on stdin exits too, so a killed test never leaves the demo running.

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

#include "Console.h"
#include "NetworkLogic.h"

static std::atomic<int> pendingKey(0);

static void readKeys(void)
{
	std::string line;
	while(std::getline(std::cin, line))
	{
		if(line.empty())
			continue;
		while(pendingKey.load())
			SLEEP(10);
		pendingKey.store(line[0]);
		if(line[0] == '0')
			return;
	}
	pendingKey.store('0');
}

static Input toInput(int key)
{
	switch(key)
	{
	case '1': return INPUT_1;
	case '2': return INPUT_2;
	case '3': return INPUT_3;
	case '4': return INPUT_4;
	case '0': return INPUT_EXIT;
	default: return INPUT_NON;
	}
}

int main(void)
{
	if(!_wgetenv(L"PHOTON_APP_ID"))
	{
		fwprintf(stderr, L"PHOTON_APP_ID not set\n");
		return 2;
	}
	static NetworkLogic networkLogic(&Console::get());
	std::thread(readKeys).detach();
	networkLogic.connect();
	for(;;)
	{
		Input input = toInput(pendingKey.exchange(0));
		networkLogic.setLastInput(input);
		networkLogic.run();
		if(input == INPUT_EXIT)
			break;
		SLEEP(50);
	}
	// Let the disconnect go out before the process ends.
	for(int i=0; i<40 && networkLogic.getState() != STATE_DISCONNECTED; ++i)
	{
		networkLogic.run();
		SLEEP(50);
	}
	Console::get().writeLine(L"demo exited");
	return 0;
}
