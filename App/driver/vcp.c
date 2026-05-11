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
    static uint32_t read_ptr = 0;
    bool connected = false;
    uint32_t write_ptr = VCP_RxBufPointer;
    uint32_t processed = 0;

    while (read_ptr != write_ptr && processed < VCP_RX_BUF_SIZE)
    {
        uint8_t b0 = VCP_RxBuf[read_ptr];
        read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE;
        processed++;

        if (b0 == 0x55)
        {
            if (read_ptr == write_ptr) break;
            uint8_t b1 = VCP_RxBuf[read_ptr];
            read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE;
            processed++;
            if (b1 == 0xAA) connected = true;
            continue;
        }

        if (b0 != 0xAA || read_ptr == write_ptr)
            continue;

        uint8_t b1 = VCP_RxBuf[read_ptr];
        read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE;
        processed++;
        if (b1 != 0x55 || read_ptr == write_ptr)
            continue;

        uint8_t type = VCP_RxBuf[read_ptr];
        read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE;
        processed++;

        if (type == VCP_TYPE_KEY || type == VCP_TYPE_KEY_LONG)
        {
            if (read_ptr == write_ptr) break;
            uint8_t key = VCP_RxBuf[read_ptr];
            read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE;
            processed++;
            if (type == VCP_TYPE_KEY) KEYBOARD_InjectKey(key); else KEYBOARD_InjectKeyLong(key);
            connected = true;
            continue;
        }

        if (read_ptr == write_ptr) break;
        uint8_t sz_hi = VCP_RxBuf[read_ptr]; read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE; processed++;
        if (read_ptr == write_ptr) break;
        uint8_t sz_lo = VCP_RxBuf[read_ptr]; read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE; processed++;
        uint16_t size = ((uint16_t)sz_hi << 8) | sz_lo;

        if (type == VCP_TYPE_KEY_BATCH && size <= 128 && (size % 2) == 0)
        {
            for (uint16_t i = 0; i < size; i += 2)
            {
                if (read_ptr == write_ptr) break;
                uint8_t key = VCP_RxBuf[read_ptr]; read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE; processed++;
                if (read_ptr == write_ptr) break;
                uint8_t flg = VCP_RxBuf[read_ptr]; read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE; processed++;
                if (flg & 0x01) KEYBOARD_InjectKeyLong(key); else KEYBOARD_InjectKey(key);
            }
            connected = true;
        }
        else
        {
            for (uint16_t i = 0; i < size && read_ptr != write_ptr; i++)
            {
                read_ptr = (read_ptr + 1) % VCP_RX_BUF_SIZE;
                processed++;
            }
        }
    }

    return connected;
}
#endif // ENABLE_FEAT_F4HWN_SCREENSHOT
