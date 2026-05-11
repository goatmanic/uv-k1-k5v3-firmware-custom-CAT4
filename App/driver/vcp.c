/* Copyright 2025 muzkr https://github.com/muzkr
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 *     Unless required by applicable law or agreed to in writing, software
 *     distributed under the License is distributed on an "AS IS" BASIS,
 *     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *     See the License for the specific language governing permissions and
 *     limitations under the License.
 *
 */

#include "driver/vcp.h"
#include "usb_config.h"
#include "py32f071_ll_bus.h"

#ifdef ENABLE_FEAT_F4HWN_SCREENSHOT
#include "driver/keyboard.h"
// Packet types for serial key injection (K5Viewer → radio)
#define VCP_TYPE_KEY       0x03
#define VCP_TYPE_KEY_LONG  0x04
#define VCP_TYPE_KEY_BATCH 0x05
#define VCP_TYPE_KEY_STATE 0x06
#endif

uint8_t VCP_RxBuf[VCP_RX_BUF_SIZE];
volatile uint32_t VCP_RxBufPointer = 0;

void VCP_Init()
{
    LL_APB1_GRP2_EnableClock(LL_APB1_GRP2_PERIPH_SYSCFG);
    LL_IOP_GRP1_EnableClock(LL_IOP_GRP1_PERIPH_GPIOA); // PA12:11
    LL_APB1_GRP1_EnableClock(LL_APB1_GRP1_PERIPH_USBD);

    cdc_acm_rx_buf_t rx_buf = {
        .buf = VCP_RxBuf,
        .size = sizeof(VCP_RxBuf),
        .write_pointer = &VCP_RxBufPointer,
    };
    cdc_acm_init(rx_buf);

    NVIC_SetPriority(USBD_IRQn, 3);
    NVIC_EnableIRQ(USBD_IRQn);
}

#ifdef ENABLE_FEAT_F4HWN_SCREENSHOT
bool VCP_ScreenshotPing(void)
{
    enum {
        PARSER_WAIT_SYNC_1 = 0,
        PARSER_WAIT_SYNC_2,
        PARSER_WAIT_TYPE,
        PARSER_WAIT_SIZE_HI,
        PARSER_WAIT_SIZE_LO,
        PARSER_WAIT_PAYLOAD,
    };

    typedef struct {
        uint8_t state;
        uint8_t type;
        uint16_t size;
        uint16_t payloadRead;
        uint8_t payloadIndex;
        uint8_t payloadLong;
    } SerialParserState_t;

    static uint32_t read_ptr = 0;
    static SerialParserState_t parser = {0};

    bool connected = false;
    uint32_t write_ptr = VCP_RxBufPointer;
    uint32_t processed = 0;

    while (read_ptr != write_ptr && processed < VCP_RX_BUF_SIZE)
    {
        const uint8_t b = VCP_RxBuf[read_ptr];
        read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE;
        processed++;

        switch (parser.state)
        {
            case PARSER_WAIT_SYNC_1:
                if (b == 0x55) {
                    connected = true;
                    parser.state = PARSER_WAIT_SYNC_2;
                } else if (b == 0xAA) {
                    parser.state = PARSER_WAIT_SYNC_2;
                }
                break;

            case PARSER_WAIT_SYNC_2:
                if (b == 0xAA) {
                    connected = true;
                    parser.state = PARSER_WAIT_TYPE;
                } else if (b == 0x55) {
                    parser.state = PARSER_WAIT_SYNC_2;
                } else {
                    parser.state = PARSER_WAIT_SYNC_1;
                }
                break;

            case PARSER_WAIT_TYPE:
                parser.type = b;
                if (b == VCP_TYPE_KEY || b == VCP_TYPE_KEY_LONG) {
                    parser.size = 1;
                    parser.payloadRead = 0;
                    parser.state = PARSER_WAIT_PAYLOAD;
                } else if (b == VCP_TYPE_KEY_STATE) {
                    parser.size = 2;
                    parser.payloadRead = 0;
                    parser.state = PARSER_WAIT_PAYLOAD;
                } else {
                    parser.state = PARSER_WAIT_SIZE_HI;
                }
                break;

            case PARSER_WAIT_SIZE_HI:
                parser.size = (uint16_t)b << 8;
                parser.state = PARSER_WAIT_SIZE_LO;
                break;

            case PARSER_WAIT_SIZE_LO:
                parser.size |= b;
                parser.payloadRead = 0;
                parser.payloadIndex = 0;
                parser.payloadLong = 0;
                if (parser.size > 256) {
                    parser.state = PARSER_WAIT_SYNC_1;
                } else if (parser.size == 0) {
                    connected = true;
                    parser.state = PARSER_WAIT_SYNC_1;
                } else {
                    parser.state = PARSER_WAIT_PAYLOAD;
                }
                break;

            case PARSER_WAIT_PAYLOAD:
                if (parser.type == VCP_TYPE_KEY || parser.type == VCP_TYPE_KEY_LONG)
                {
                    if (b < KEY_INVALID) {
                        if (parser.type == VCP_TYPE_KEY) KEYBOARD_InjectKey(b); else KEYBOARD_InjectKeyLong(b);
                        connected = true;
                    }
                }
                else if (parser.type == VCP_TYPE_KEY_STATE)
                {
                    if (parser.payloadRead == 0u) {
                            parser.payloadIndex = b;
                    } else {
                            parser.payloadLong = b;
                            if (parser.payloadIndex < KEY_INVALID) {
                                KEYBOARD_SetSerialKeyState(parser.payloadIndex, (parser.payloadLong & 0x01u) != 0u, (parser.payloadLong & 0x02u) != 0u);
                                connected = true;
                            }
                    }
                }
                else if (parser.type == VCP_TYPE_KEY_BATCH)
                {
                    if ((parser.size & 1u) == 0u && parser.size <= 128)
                    {
                        if ((parser.payloadRead & 1u) == 0u) {
                            parser.payloadIndex = b;
                        } else {
                            parser.payloadLong = b;
                            if (parser.payloadIndex < KEY_INVALID) {
                                if (parser.payloadLong & 0x01) KEYBOARD_InjectKeyLong(parser.payloadIndex);
                                else KEYBOARD_InjectKey(parser.payloadIndex);
                                connected = true;
                            }
                        }
                    }
                }

                parser.payloadRead++;
                if (parser.payloadRead >= parser.size)
                    parser.state = PARSER_WAIT_SYNC_1;
                break;

            default:
                parser.state = PARSER_WAIT_SYNC_1;
                break;
        }
    }

    return connected;
}
#endif // ENABLE_FEAT_F4HWN_SCREENSHOT
